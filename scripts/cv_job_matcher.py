"""
cv_job_matcher.py -- CV <-> Job Matching & Acceptance Likelihood Scoring.

v2: Profile-aware matching using the skill taxonomy for semantic understanding.
Uses taxonomy-based skill extraction, synonym-aware matching, experience level
detection, and profile preferences (desired roles, locations, job types).
Falls back to TF-IDF when descriptions are rich enough.
"""

import re
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import yaml
from rapidfuzz import fuzz

# Semantic matching for "never miss" rescue
try:
    from semantic_matcher import SemanticMatcher
    SEMANTIC_AVAILABLE = True
except ImportError:
    SEMANTIC_AVAILABLE = False

from cv_parser import CVData
from scrapers.base import JobPosting

# New: taxonomy-based skill matching
from skill_taxonomy import (
    extract_skills_from_text,
    skills_match_score,
    ExtractedSkill,
)

# Try importing profile system (may not be available in standalone mode)
try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from user_profile import detect_experience_level
    PROFILE_AVAILABLE = True
except ImportError:
    PROFILE_AVAILABLE = False


@dataclass
class MatchResult:
    job: JobPosting
    overall_score: float  # 0-100
    skill_score: float  # 0-100  (taxonomy-based)
    role_score: float  # 0-100  (how well title matches desired roles)
    location_score: float  # 0-100  (location preference match)
    type_score: float  # 0-100  (job type match)
    language_penalty: float  # negative
    experience_fit: float  # 0-100  (experience level match)
    confidence: str  # "Low", "Medium", "High", "Very High"
    matched_skills: List[str] = field(default_factory=list)
    missing_skills: List[str] = field(default_factory=list)
    match_reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # Backward compatibility aliases
    @property
    def keyword_score(self) -> float:
        return self.skill_score

    @property
    def tfidf_score(self) -> float:
        return self.role_score

    @property
    def role_type_bonus(self) -> float:
        return self.type_score * 0.15

    @property
    def location_bonus(self) -> float:
        return self.location_score * 0.1

    def to_dict(self) -> dict:
        return {
            "job_title": self.job.title,
            "company": self.job.company,
            "location": self.job.location,
            "url": self.job.url,
            "source": self.job.source,
            "posted_date": self.job.posted_date or "",
            "overall_score": round(self.overall_score, 1),
            "skill_score": round(self.skill_score, 1),
            "role_score": round(self.role_score, 1),
            "location_score": round(self.location_score, 1),
            "type_score": round(self.type_score, 1),
            "experience_fit": round(self.experience_fit, 1),
            "confidence": self.confidence,
            "matched_skills": self.matched_skills,
            "missing_skills": self.missing_skills,
            "match_reasons": self.match_reasons,
            "warnings": self.warnings,
            # Backward compat
            "keyword_score": round(self.skill_score, 1),
            "tfidf_score": round(self.role_score, 1),
        }


@dataclass
class SkillsGapAnalysis:
    """Aggregate analysis of skills missing across all matched jobs."""
    missing_skills_frequency: Dict[str, int] = field(default_factory=dict)
    cv_skills: List[str] = field(default_factory=list)
    top_missing: List[str] = field(default_factory=list)


# -----------------------------------------------------------------
# Config loading (backward compat)
# -----------------------------------------------------------------

def _load_config_from_project_root() -> dict:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, "config.yaml")
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _matching_config(config: Optional[dict]) -> dict:
    data = config if isinstance(config, dict) else _load_config_from_project_root()
    matching = data.get("matching", {}) if isinstance(data, dict) else {}
    return matching if isinstance(matching, dict) else {}


# -----------------------------------------------------------------
# Skill extraction (now taxonomy-powered)
# -----------------------------------------------------------------

def extract_job_skills(text: str, config: Optional[dict] = None) -> Set[str]:
    """Extract technical skills mentioned in a job description using taxonomy."""
    extracted = extract_skills_from_text(text, source="job")
    return set(e.canonical.lower() for e in extracted)


# -----------------------------------------------------------------
# German language detection
# -----------------------------------------------------------------

_GERMAN_REQUIRED_PATTERNS = [
    re.compile(r"deutsch(?:kenntnisse|e?\s+sprach)?\s*(?:auf\s+)?(?:niveau\s*)?([abc][12])", re.IGNORECASE),
    re.compile(r"german\s+(?:language\s+)?(?:level\s+)?([abc][12])", re.IGNORECASE),
    re.compile(r"flie[ss\u00df]end(?:e[srmn]?)?\s+deutsch", re.IGNORECASE),
    re.compile(r"deutsch\s+(?:als\s+)?muttersprach", re.IGNORECASE),
    re.compile(r"verhandlungssicher(?:e[srmn]?)?\s+deutsch", re.IGNORECASE),
    re.compile(r"sehr\s+gute?\s+deutsch", re.IGNORECASE),
    re.compile(r"gute?\s+deutsch", re.IGNORECASE),
]

_GERMAN_PREFERRED_PATTERNS = [
    re.compile(r"german\s+(?:is\s+)?(?:a\s+)?(?:plus|advantage|asset|beneficial)", re.IGNORECASE),
    re.compile(r"deutsch(?:kenntnisse)?\s+(?:w[aue]ren?|sind?)\s+(?:von\s+)?vorteil", re.IGNORECASE),
    re.compile(r"ideally\s+(?:also\s+)?(?:speaking?\s+)?german", re.IGNORECASE),
]

_LEVEL_MAP = {"a1": 1, "a2": 2, "b1": 3, "b2": 4, "c1": 5, "c2": 6}


def detect_german_requirement(text: str, config: Optional[dict] = None) -> tuple:
    """
    Detect if job requires German language proficiency.
    Returns (requires_german: bool, detected_level: str).
    """
    text_check = text[:3000]  # Only check first 3000 chars for efficiency

    # Check hard requirements
    for pattern in _GERMAN_REQUIRED_PATTERNS:
        match = pattern.search(text_check)
        if match:
            # Try to extract specific level
            level_match = re.search(r"[abc][12]", match.group(), re.IGNORECASE)
            if level_match:
                return True, level_match.group().upper()
            # "fliessend" / "verhandlungssicher" implies C1
            if any(kw in match.group().lower() for kw in ["flie", "verhandlung", "muttersprac"]):
                return True, "C1"
            if "sehr gut" in match.group().lower():
                return True, "B2"
            if "gut" in match.group().lower():
                return True, "B1"
            return True, "B2"  # Default assumption

    # Check preferred/nice-to-have
    for pattern in _GERMAN_PREFERRED_PATTERNS:
        if pattern.search(text_check):
            return True, "preferred"

    # Loose check: entire description in German?
    german_word_count = len(re.findall(r"\b(?:und|oder|die|das|der|mit|von|ist|als|bei|auf|aus|nach)\b", text_check.lower()))
    if german_word_count > 10:
        # Description is in German -> likely requires German
        return True, "B1"

    return False, ""


def compute_language_penalty(
    job: JobPosting,
    current_german_level: str = "A2",
    config: Optional[dict] = None,
) -> tuple:
    """
    Penalty if job requires German proficiency above current level.
    Returns (penalty, warnings).
    """
    text = f"{job.title} {job.description}".strip()
    if not text:
        return 0.0, []

    requires_german, required_level = detect_german_requirement(text, config=config)

    if not requires_german:
        return 0.0, []

    current = _LEVEL_MAP.get(current_german_level.lower(), 2)

    if required_level == "preferred":
        return -5.0, [f"German preferred (you have {current_german_level})"]

    required = _LEVEL_MAP.get(required_level.lower(), 4)

    if current >= required:
        return 0.0, []
    else:
        gap = required - current
        penalty = gap * 8  # -8 per level gap
        return -penalty, [
            f"Requires German {required_level.upper()} (you have {current_german_level} -- {gap} level gap)"
        ]


# -----------------------------------------------------------------
# Score components
# -----------------------------------------------------------------

def compute_skill_score(
    cv_skill_names: List[str], job_text: str
) -> tuple:
    """
    Taxonomy-aware skill matching.
    Returns (score 0-100, matched_skills, missing_skills, reasons).
    """
    # Extract skills from job description
    job_skills_extracted = extract_skills_from_text(job_text, source="job")
    job_skill_names = [e.canonical.lower() for e in job_skills_extracted]

    if not job_skill_names:
        # No skills detected in job -> can't score, return neutral
        return 50.0, [], [], ["No specific skills detected in job description"]

    # Use taxonomy semantic matching
    score, matched, missing = skills_match_score(cv_skill_names, job_skill_names)

    reasons = []
    if matched:
        reasons.append(f"Skills match: {', '.join(matched[:5])}")
    if missing:
        reasons.append(f"Consider learning: {', '.join(missing[:3])}")

    return score, matched, missing, reasons


def compute_role_score(
    job: JobPosting,
    desired_roles: List[str],
    cv_skill_names: List[str],
) -> tuple:
    """
    How well does the job title/description match desired roles?
    Returns (score 0-100, reasons).
    """
    if not desired_roles:
        return 50.0, []  # No preference -> neutral

    job_text = f"{job.title} {job.job_type or ''}".lower()
    best_score = 0
    best_role = ""

    for role in desired_roles:
        # Fuzzy match role against title
        ratio = fuzz.token_set_ratio(role.lower(), job_text)
        if ratio > best_score:
            best_score = ratio
            best_role = role

    # Also check for skill-based role inference
    # e.g., if job mentions "ETL", "Airflow", "data pipeline" -> likely data engineering
    job_full = f"{job.title} {job.description[:500]}".lower()
    role_skill_boost = 0
    for role in desired_roles:
        role_lower = role.lower()
        # Direct mention of role in description
        if role_lower in job_full:
            role_skill_boost = max(role_skill_boost, 20)

    score = min(100, best_score + role_skill_boost)
    reasons = []
    if best_score > 60:
        reasons.append(f"Title matches desired role: {best_role}")
    elif best_score > 40:
        reasons.append(f"Partial role match: {best_role}")

    return score, reasons


def compute_location_score(
    job: JobPosting, desired_locations: List[str]
) -> tuple:
    """
    Score based on location preference match.
    Returns (score 0-100, reasons).
    """
    if not desired_locations:
        return 50.0, []  # No preference

    job_loc = job.location.lower()

    # Check for remote
    if "remote" in job_loc:
        return 90.0, ["Remote position"]

    for loc in desired_locations:
        if loc.lower() in job_loc:
            return 100.0, [f"In preferred location: {loc}"]

    # Check for Germany at least
    if "germany" in job_loc or "deutschland" in job_loc:
        return 40.0, ["In Germany, but not preferred city"]

    return 20.0, ["Location does not match preferences"]


def compute_type_score(
    job: JobPosting, desired_types: List[str]
) -> tuple:
    """
    Score based on job type match (Werkstudent, Thesis, etc.)
    Returns (score 0-100, reasons).
    """
    if not desired_types:
        return 50.0, []

    job_text = f"{job.title} {job.job_type or ''} {job.description[:300]}".lower()

    for jt in desired_types:
        if jt.lower() in job_text:
            return 100.0, [f"Job type matches: {jt}"]

    # Check for related types
    type_aliases = {
        "werkstudent": ["working student", "studentische hilfskraft", "student job"],
        "working student": ["werkstudent", "studentische hilfskraft"],
        "internship": ["praktikum", "intern"],
        "praktikum": ["internship", "intern"],
        "master thesis": ["masterarbeit", "abschlussarbeit", "thesis"],
        "masterarbeit": ["master thesis", "abschlussarbeit", "thesis"],
    }

    for jt in desired_types:
        aliases = type_aliases.get(jt.lower(), [])
        for alias in aliases:
            if alias in job_text:
                return 90.0, [f"Job type matches (alias): {jt}"]

    return 20.0, ["Job type does not match preferences"]


def compute_experience_fit(
    job: JobPosting, user_experience_level: str = "entry"
) -> tuple:
    """
    Does the job's experience requirement match the user's level?
    Returns (score 0-100, reasons).
    """
    if not PROFILE_AVAILABLE:
        return 70.0, []

    job_text = f"{job.title} {job.description[:500]}"
    job_exp = detect_experience_level(job_text)
    job_level = job_exp["level"]

    if job_level == "unknown":
        return 70.0, []  # Can't determine, assume moderate fit

    level_map = {"entry": 1, "mid": 2, "senior": 3}
    user_val = level_map.get(user_experience_level, 1)
    job_val = level_map.get(job_level, 1)

    if user_val == job_val:
        return 100.0, [f"Experience level matches: {job_level}"]
    elif user_val > job_val:
        return 80.0, [f"You may be overqualified ({user_experience_level} for {job_level} role)"]
    else:
        gap = job_val - user_val
        score = max(20, 100 - gap * 35)
        return score, [f"Requires {job_level} level (you are {user_experience_level})"]


def get_confidence_label(score: float) -> str:
    """Map overall score to confidence label."""
    if score >= 75:
        return "Very High"
    elif score >= 55:
        return "High"
    elif score >= 35:
        return "Medium"
    return "Low"


# -----------------------------------------------------------------
# Main matcher (v2 — profile-aware)
# -----------------------------------------------------------------

def match_cv_to_jobs(
    cv: CVData,
    jobs: List[JobPosting],
    target_cities: List[str] = None,
    target_types: List[str] = None,
    current_german_level: str = "A2",
    config: Optional[dict] = None,
    cv_skills_override: Optional[List[str]] = None,
    # v2: profile-aware parameters
    desired_roles: Optional[List[str]] = None,
    experience_level: Optional[str] = None,
    profile_skills: Optional[List[dict]] = None,
) -> List[MatchResult]:
    """
    Match a CV against a list of job postings and score acceptance likelihood.
    
    v2 additions:
    - desired_roles: what roles the user actually wants
    - experience_level: "entry", "mid", "senior"
    - profile_skills: skills from the profile (with categories + levels)
    
    Returns list of MatchResult sorted by overall score (descending).
    """
    cfg = config if isinstance(config, dict) else _load_config_from_project_root()
    matching = _matching_config(cfg)
    matching_defaults = cfg.get("_matching_defaults", {}) if isinstance(cfg, dict) else {}

    rescue_skill_max = float(matching.get("semantic_rescue_skill_max", 40))
    rescue_semantic_min = float(matching.get("semantic_rescue_semantic_min", 50))
    rescue_weight = float(matching.get("semantic_rescue_weight", 0.85))
    diagnostics_enabled = bool(matching.get("diagnostics", False))
    role_score_floor = float(matching.get("role_score_floor", 0))
    role_score_penalty = float(matching.get("role_score_penalty", 0))
    role_keyword_required = bool(matching.get("role_keyword_required", False))
    role_keyword_required_penalty = float(matching.get("role_keyword_required_penalty", 0))
    global_negatives = [
        str(k).strip().lower()
        for k in matching_defaults.get("negative_keywords", [])
        if str(k).strip()
    ]
    local_negatives = [
        str(k).strip().lower()
        for k in matching.get("negative_keywords", [])
        if str(k).strip()
    ]
    negative_keywords = list(dict.fromkeys(global_negatives + local_negatives))
    negative_penalty = float(matching.get("negative_keyword_penalty", 25))

    if target_cities is None:
        raw_cities = cfg.get("cities", []) if isinstance(cfg, dict) else []
        target_cities = [str(c).strip() for c in raw_cities if str(c).strip()]
    if target_types is None:
        raw_types = cfg.get("job_types", []) if isinstance(cfg, dict) else []
        target_types = [str(t).strip() for t in raw_types if str(t).strip()]
    if not current_german_level:
        current_german_level = str(matching.get("default_german_level", "A2"))
    if desired_roles is None:
        desired_roles = []
    if experience_level is None:
        experience_level = "entry"

    # Build CV skill list from taxonomy
    if profile_skills:
        # Use profile skills (user-confirmed + extracted)
        cv_skill_names = [s.get("name", "").lower() for s in profile_skills if isinstance(s, dict)]
    elif cv_skills_override:
        cv_skill_names = [s.lower() for s in cv_skills_override]
    else:
        # Extract from CV text using taxonomy
        extracted = extract_skills_from_text(cv.raw_text, source="cv")
        cv_skill_names = [e.canonical.lower() for e in extracted]

    results = []

    # Build semantic matcher for rescue mechanism (initialized once per run)
    _semantic_matcher = None
    if SEMANTIC_AVAILABLE:
        try:
            yaml_skills = cfg.get("cv_skills", []) if isinstance(cfg, dict) else []
            yaml_keywords = cfg.get("search_keywords", []) if isinstance(cfg, dict) else []
            semantic_embeddings_enabled = bool(matching.get("semantic_embeddings_enabled", False))
            semantic_embeddings_enabled = semantic_embeddings_enabled or os.environ.get(
                "ATS_ENABLE_SENTENCE_EMBEDDINGS", ""
            ).strip().lower() in {"1", "true", "yes", "on"}
            _semantic_matcher = SemanticMatcher(
                cv_text=cv.raw_text,
                yaml_skills=[str(s) for s in yaml_skills] if yaml_skills else cv_skill_names,
                yaml_keywords=[str(k) for k in yaml_keywords] if yaml_keywords else [],
                enable_embeddings=semantic_embeddings_enabled,
            )
            print(f"[Matcher] Semantic matcher initialized (tier: {_semantic_matcher.tier})")
        except Exception as e:
            print(f"[Matcher] Semantic matcher failed to init: {e}")
            _semantic_matcher = None

    # Weights for the final score
    W_SKILL = 0.35    # Skill match is most important
    W_ROLE = 0.25     # Role relevance
    W_TYPE = 0.15     # Job type match
    W_LOCATION = 0.10 # Location preference
    W_EXPERIENCE = 0.10  # Experience level
    W_TITLE_SIM = 0.05   # Fuzzy title similarity

    def _score_one(job: JobPosting) -> MatchResult:
        job_text = f"{job.title} {job.description} {' '.join(job.tags or [])}".strip()
        job_text_lower = job_text.lower()
        all_reasons = []

        # 1. Taxonomy-powered skill matching (35%)
        skill_score, matched, missing, skill_reasons = compute_skill_score(
            cv_skill_names, job_text
        )
        skill_score_raw = skill_score
        sem_score = None
        sem_rescued = False
        sem_tier = _semantic_matcher.tier if _semantic_matcher is not None else "none"

        if _semantic_matcher is not None:
            if diagnostics_enabled or skill_score < rescue_skill_max:
                sem_score = _semantic_matcher.score(job_text)

        # Semantic rescue: if taxonomy found few matches, check semantic similarity
        if sem_score is not None and skill_score < rescue_skill_max:
            if sem_score > rescue_semantic_min:
                rescued_score = max(skill_score, sem_score * rescue_weight)
                skill_reasons.append(f"Semantic rescue: {sem_score:.0f}% text similarity ({sem_tier})")
                skill_score = rescued_score
                sem_rescued = True

        all_reasons.extend(skill_reasons)

        # 2. Role relevance (25%)
        role_score, role_reasons = compute_role_score(job, desired_roles, cv_skill_names)
        all_reasons.extend(role_reasons)
        role_keyword_hit = False
        if desired_roles:
            role_keyword_hit = any(role.lower() in job_text_lower for role in desired_roles)

        # 3. Job type match (15%)
        type_score, type_reasons = compute_type_score(job, target_types)
        all_reasons.extend(type_reasons)

        # 4. Location preference (10%)
        loc_score, loc_reasons = compute_location_score(job, target_cities)
        all_reasons.extend(loc_reasons)

        # 5. Experience level fit (10%)
        exp_score, exp_reasons = compute_experience_fit(job, experience_level)
        all_reasons.extend(exp_reasons)

        # 6. Title fuzzy similarity bonus (5%)
        title_query = " ".join(desired_roles[:3] + cv_skill_names[:5]) if desired_roles else " ".join(cv_skill_names[:8])
        title_sim = fuzz.token_set_ratio(title_query, job.title)

        # 7. German language penalty (subtracted from total)
        lang_penalty, warnings = compute_language_penalty(
            job, current_german_level, config=cfg,
        )

        negative_hits = []
        if negative_keywords:
            negative_hits = [kw for kw in negative_keywords if kw in job_text_lower]
            if negative_hits:
                warnings = list(warnings) + [
                    "Negative keywords: {hits}".format(hits=", ".join(negative_hits[:3]))
                ]

        if diagnostics_enabled:
            sem_display = f"{sem_score:.1f}" if sem_score is not None else "n/a"
            warnings = list(warnings) + [
                "diag: tier={tier} sem={sem} skill={skill:.1f} rescue={rescue}".format(
                    tier=sem_tier,
                    sem=sem_display,
                    skill=skill_score_raw,
                    rescue="yes" if sem_rescued else "no",
                )
            ]

        # Weighted total
        overall = (
            skill_score * W_SKILL
            + role_score * W_ROLE
            + type_score * W_TYPE
            + loc_score * W_LOCATION
            + exp_score * W_EXPERIENCE
            + title_sim * W_TITLE_SIM
            + lang_penalty
        )
        if negative_hits:
            overall -= negative_penalty
        if role_score_floor and role_score < role_score_floor:
            overall -= role_score_penalty
            warnings = list(warnings) + [
                "Low role relevance: {score:.1f}% (<{floor:.0f})".format(
                    score=role_score,
                    floor=role_score_floor,
                )
            ]
        if role_keyword_required and desired_roles and not role_keyword_hit:
            overall -= role_keyword_required_penalty
            warnings = list(warnings) + ["Role keyword missing"]
        overall = max(0, min(100, overall))

        return MatchResult(
            job=job,
            overall_score=overall,
            skill_score=skill_score,
            role_score=role_score,
            location_score=loc_score,
            type_score=type_score,
            language_penalty=lang_penalty,
            experience_fit=exp_score,
            confidence=get_confidence_label(overall),
            matched_skills=matched,
            missing_skills=missing,
            match_reasons=all_reasons,
            warnings=warnings,
        )

    workers = min(16, max(1, len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_score_one, job): job for job in jobs}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                pass

    # Sort by overall score descending
    results.sort(key=lambda r: r.overall_score, reverse=True)
    return results


def analyze_skills_gap(matches: List[MatchResult], cv_skills: List[str]) -> SkillsGapAnalysis:
    """Aggregate skills gap analysis across all matched jobs."""
    frequency: Dict[str, int] = {}

    for match in matches:
        for skill in match.missing_skills:
            frequency[skill] = frequency.get(skill, 0) + 1

    # Sort by frequency
    sorted_missing = sorted(frequency.items(), key=lambda x: x[1], reverse=True)
    top_missing = [skill for skill, _ in sorted_missing[:15]]

    return SkillsGapAnalysis(
        missing_skills_frequency=frequency,
        cv_skills=cv_skills,
        top_missing=top_missing,
    )


if __name__ == "__main__":
    import sys
    from cv_parser import parse_cv
    from job_scraper import get_cached_jobs

    if len(sys.argv) < 2:
        print("Usage: python cv_job_matcher.py <path_to_cv.pdf>")
        sys.exit(1)

    cv = parse_cv(sys.argv[1])
    jobs = get_cached_jobs()

    if not jobs:
        print("No cached jobs found. Run job_scraper.py first.")
        sys.exit(1)

    matches = match_cv_to_jobs(cv, jobs)
    gap = analyze_skills_gap(matches, cv.skills)

    print(f"\nTop {min(10, len(matches))} matches:")
    print("=" * 60)

    for i, m in enumerate(matches[:10], 1):
        print(f"\n{i}. {m.job.title} @ {m.job.company}")
        print(f"   Score: {m.overall_score:.1f}% ({m.confidence})")
        print(f"   Skills: {m.skill_score:.0f}% | Role: {m.role_score:.0f}% | Type: {m.type_score:.0f}%")
        print(f"   Location: {m.job.location} | Source: {m.job.source}")
        if m.matched_skills:
            print(f"   Matched: {', '.join(m.matched_skills[:8])}")
        if m.missing_skills:
            print(f"   Missing: {', '.join(m.missing_skills[:5])}")
        if m.match_reasons:
            print(f"   Reasons: {'; '.join(m.match_reasons[:3])}")
        if m.warnings:
            print(f"   Warnings: {'; '.join(m.warnings)}")

    if gap.top_missing:
        print(f"\n{'='*60}")
        print("Skills Gap -- Most requested skills you're missing:")
        for skill in gap.top_missing[:10]:
            count = gap.missing_skills_frequency[skill]
            print(f"  - {skill} (requested in {count} jobs)")
