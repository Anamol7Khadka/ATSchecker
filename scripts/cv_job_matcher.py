"""
cv_job_matcher.py — CV ↔ Job Matching & Acceptance Likelihood Scoring.
Uses TF-IDF cosine similarity, keyword overlap, and heuristic bonuses.
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from cv_parser import CVData
from scrapers.base import JobPosting


@dataclass
class MatchResult:
    job: JobPosting
    overall_score: float  # 0–100
    keyword_score: float  # 0–100
    tfidf_score: float  # 0–100
    role_type_bonus: float
    location_bonus: float
    language_penalty: float
    confidence: str  # "Low", "Medium", "High", "Very High"
    matched_skills: List[str] = field(default_factory=list)
    missing_skills: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "job_title": self.job.title,
            "company": self.job.company,
            "location": self.job.location,
            "url": self.job.url,
            "source": self.job.source,
            "overall_score": round(self.overall_score, 1),
            "keyword_score": round(self.keyword_score, 1),
            "tfidf_score": round(self.tfidf_score, 1),
            "confidence": self.confidence,
            "matched_skills": self.matched_skills,
            "missing_skills": self.missing_skills,
            "warnings": self.warnings,
        }


@dataclass
class SkillsGapAnalysis:
    """Aggregate analysis of skills missing across all matched jobs."""
    missing_skills_frequency: Dict[str, int] = field(default_factory=dict)
    cv_skills: List[str] = field(default_factory=list)
    top_missing: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# Keyword extraction
# ─────────────────────────────────────────────────────────────

# Common tech skills/tools to look for in job descriptions
TECH_KEYWORDS = {
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "rust",
    "ruby", "scala", "kotlin", "swift", "r", "sql", "bash", "shell",
    "react", "angular", "vue", "node.js", "django", "flask", "fastapi",
    "spring", "spring boot", ".net", "express",
    "pandas", "numpy", "scikit-learn", "pytorch", "tensorflow", "keras",
    "spark", "pyspark", "hadoop", "kafka", "airflow", "dbt", "flink",
    "elasticsearch", "solr", "redis", "mongodb", "postgresql", "mysql",
    "cassandra", "dynamodb", "neo4j",
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform",
    "ci/cd", "jenkins", "gitlab", "github actions",
    "linux", "git", "maven", "gradle",
    "machine learning", "deep learning", "nlp", "computer vision",
    "data engineering", "data science", "etl", "elt",
    "rest api", "graphql", "grpc", "microservices",
    "agile", "scrum", "jira",
    "langchain", "llm", "rag", "protobuf",
}

# German language requirement patterns
GERMAN_PATTERNS = [
    r"deutsch.{0,20}(?:flie[sß]end|verhandlungssicher|muttersprachlich|c[12]|b[12])",
    r"german.{0,20}(?:fluent|native|advanced|proficient|required|mandatory|b[12]|c[12])",
    r"(?:flie[sß]end|verhandlungssicher).{0,20}deutsch",
    r"(?:fluent|native|advanced|proficient).{0,20}german",
    r"deutschkenntnisse.{0,20}(?:erforderlich|zwingend|notwendig|vorausgesetzt)",
    r"german.{0,20}(?:is a must|required|mandatory|essential)",
    r"sehr gute.{0,20}deutschkenntnisse",
    r"(?:b2|c1|c2).{0,10}(?:german|deutsch)",
]


def extract_job_skills(text: str) -> Set[str]:
    """Extract technical skills mentioned in a job description."""
    text_lower = text.lower()
    found = set()
    for skill in TECH_KEYWORDS:
        if len(skill) <= 2:
            if re.search(r"\b" + re.escape(skill) + r"\b", text_lower):
                found.add(skill)
        else:
            if skill in text_lower:
                found.add(skill)
    return found


def detect_german_requirement(text: str) -> tuple[bool, str]:
    """
    Detect if job requires German language proficiency.
    Returns (requires_german, detected_level).
    """
    text_lower = text.lower()

    for pattern in GERMAN_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            matched_text = match.group()
            # Try to detect level
            level_match = re.search(r"[bc][12]", matched_text)
            level = level_match.group().upper() if level_match else "B2+"
            return True, level

    # Also check for basic mentions
    if "german" in text_lower or "deutsch" in text_lower:
        # Check context
        for phrase in ["german preferred", "german is a plus", "deutsch von vorteil"]:
            if phrase in text_lower:
                return True, "preferred"

    return False, ""


# ─────────────────────────────────────────────────────────────
# Matching functions
# ─────────────────────────────────────────────────────────────

def compute_keyword_score(cv_skills: Set[str], job_skills: Set[str]) -> tuple[float, List[str], List[str]]:
    """
    Compute keyword overlap score between CV skills and job requirements.
    Returns (score, matched_skills, missing_skills).
    """
    if not job_skills:
        return 50.0, [], []  # No keywords to match → neutral score

    matched = cv_skills & job_skills
    missing = job_skills - cv_skills

    # Score based on coverage ratio, with a slight boost for high overlap
    coverage = len(matched) / len(job_skills) if job_skills else 0
    score = min(100, coverage * 100 * 1.1)  # 10% boost

    return score, sorted(matched), sorted(missing)


def compute_tfidf_score(cv_text: str, job_text: str) -> float:
    """Compute TF-IDF cosine similarity between CV and job description."""
    if not cv_text.strip() or not job_text.strip():
        return 0.0

    try:
        vectorizer = TfidfVectorizer(
            stop_words="english",
            max_features=5000,
            ngram_range=(1, 2),
        )
        tfidf_matrix = vectorizer.fit_transform([cv_text, job_text])
        sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
        return min(100, sim * 100 * 2)  # Scale up since CV vs. job desc typically have low cos-sim
    except Exception:
        return 0.0


def compute_tfidf_scores_batch(cv_text: str, job_texts: List[str]) -> List[float]:
    """Compute TF-IDF cosine scores for all jobs against one CV in a single fit/transform."""
    if not cv_text.strip() or not job_texts:
        return [0.0 for _ in job_texts]

    try:
        vectorizer = TfidfVectorizer(
            stop_words="english",
            max_features=5000,
            ngram_range=(1, 2),
        )
        docs = [cv_text] + job_texts
        tfidf_matrix = vectorizer.fit_transform(docs)
        sims = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])[0]
        return [min(100, float(sim) * 100 * 2) for sim in sims]
    except Exception:
        return [0.0 for _ in job_texts]


def compute_role_type_bonus(job: JobPosting, target_types: List[str]) -> float:
    """Bonus if job type matches target role types."""
    if not target_types:
        return 0.0

    job_text = f"{job.title} {job.job_type} {job.description}".lower()
    for jt in target_types:
        if jt.lower() in job_text:
            return 10.0  # +10 bonus
    return 0.0


def compute_location_bonus(job: JobPosting, target_cities: List[str]) -> float:
    """Bonus if job is in one of the target cities."""
    job_loc = job.location.lower()
    for city in target_cities:
        if city.lower() in job_loc:
            return 5.0  # +5 bonus
    return 0.0


def compute_language_penalty(job: JobPosting, current_german_level: str = "A2") -> tuple[float, List[str]]:
    """
    Penalty if job requires German proficiency above current level.
    Returns (penalty, warnings).
    """
    text = f"{job.title} {job.description}".strip()
    if not text:
        return 0.0, []

    requires_german, required_level = detect_german_requirement(text)

    if not requires_german:
        return 0.0, []

    # Level hierarchy
    levels = {"a1": 1, "a2": 2, "b1": 3, "b2": 4, "c1": 5, "c2": 6}
    current = levels.get(current_german_level.lower(), 2)

    if required_level == "preferred":
        return -5.0, [f"German preferred (you have {current_german_level})"]

    required = levels.get(required_level.lower(), 4)  # Default B2 if unclear

    if current >= required:
        return 0.0, []
    else:
        gap = required - current
        penalty = gap * 8  # -8 per level gap
        level_name = required_level.upper()
        return -penalty, [
            f"Requires German {level_name} (you have {current_german_level} — {gap} level(s) gap)"
        ]


def get_confidence_label(score: float) -> str:
    """Map overall score to confidence label."""
    if score >= 75:
        return "Very High"
    elif score >= 55:
        return "High"
    elif score >= 35:
        return "Medium"
    return "Low"


# ─────────────────────────────────────────────────────────────
# Main matcher
# ─────────────────────────────────────────────────────────────

def match_cv_to_jobs(
    cv: CVData,
    jobs: List[JobPosting],
    target_cities: List[str] = None,
    target_types: List[str] = None,
    current_german_level: str = "A2",
) -> List[MatchResult]:
    """
    Match a CV against a list of job postings and score acceptance likelihood.

    Returns list of MatchResult sorted by overall score (descending).
    """
    if target_cities is None:
        target_cities = ["Berlin", "Wolfsburg", "Leipzig"]
    if target_types is None:
        target_types = ["Werkstudent", "Working Student", "Internship", "Praktikum"]

    cv_skills_set = set(s.lower() for s in cv.skills)
    results = []

    job_texts = [f"{job.title} {job.description} {' '.join(job.tags)}".strip() for job in jobs]
    tfidf_scores = compute_tfidf_scores_batch(cv.raw_text, job_texts)

    def _score_one(index: int, job: JobPosting, job_text: str) -> MatchResult:
        job_skills = extract_job_skills(job_text)

        # 1. Keyword overlap score (weight: 40%)
        kw_score, matched, missing = compute_keyword_score(cv_skills_set, job_skills)

        # 2. TF-IDF similarity (weight: 30%)
        tfidf_score = tfidf_scores[index] if index < len(tfidf_scores) else 0.0

        # 3. Role type bonus (weight: flat bonus)
        role_bonus = compute_role_type_bonus(job, target_types)

        # 4. Location bonus (weight: flat bonus)
        loc_bonus = compute_location_bonus(job, target_cities)

        # 5. German language penalty
        lang_penalty, warnings = compute_language_penalty(job, current_german_level)

        title_match = fuzz.token_set_ratio(
            " ".join(target_types + cv.skills[:5]),
            job.title,
        )

        overall = (
            kw_score * 0.40
            + tfidf_score * 0.30
            + title_match * 0.20
            + 10
            + role_bonus
            + loc_bonus
            + lang_penalty
        )
        overall = max(0, min(100, overall))

        return MatchResult(
            job=job,
            overall_score=overall,
            keyword_score=kw_score,
            tfidf_score=tfidf_score,
            role_type_bonus=role_bonus,
            location_bonus=loc_bonus,
            language_penalty=lang_penalty,
            confidence=get_confidence_label(overall),
            matched_skills=matched,
            missing_skills=missing,
            warnings=warnings,
        )

    workers = min(16, max(1, len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {
            pool.submit(_score_one, idx, job, job_texts[idx]): idx
            for idx, job in enumerate(jobs)
        }
        for future in as_completed(future_to_idx):
            results.append(future.result())

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
        print(f"   Location: {m.job.location} | Source: {m.job.source}")
        if m.matched_skills:
            print(f"   Matched: {', '.join(m.matched_skills[:8])}")
        if m.missing_skills:
            print(f"   Missing: {', '.join(m.missing_skills[:5])}")
        if m.warnings:
            print(f"   ⚠ {'; '.join(m.warnings)}")

    if gap.top_missing:
        print(f"\n{'='*60}")
        print("Skills Gap — Most requested skills you're missing:")
        for skill in gap.top_missing[:10]:
            count = gap.missing_skills_frequency[skill]
            print(f"  • {skill} (requested in {count} jobs)")
