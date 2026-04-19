"""
profile.py — User Profile System for ATSchecker.

Builds a rich profile from CV extraction + user input.
The profile drives matching, scraping, and the entire UX.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Add scripts/ to path for imports
SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
sys.path.insert(0, SCRIPT_DIR)

from skill_taxonomy import extract_skills_from_text, ExtractedSkill, get_skill_display_name


# ─── Experience Level Detection ──────────────────────────────────────

import re

_EXPERIENCE_PATTERNS = [
    # "5+ years", "3-5 years", "mindestens 3 Jahre"
    (re.compile(r"(\d+)\+?\s*(?:years?|jahre?|yrs?)\s*(?:of\s+)?(?:experience|erfahrung|berufserfahrung)", re.IGNORECASE), "years"),
    (re.compile(r"(\d+)\s*[-–]\s*(\d+)\s*(?:years?|jahre?)\s*(?:of\s+)?(?:experience|erfahrung)", re.IGNORECASE), "range"),
    # "senior", "lead", "principal"
    (re.compile(r"\b(?:senior|lead|principal|staff|expert)\b", re.IGNORECASE), "senior"),
    # "junior", "entry level", "graduate", "werkstudent"
    (re.compile(r"\b(?:junior|entry.?level|graduate|werkstudent|working.?student|intern|praktikant|trainee|berufseinsteiger)\b", re.IGNORECASE), "entry"),
    # "mid-level", "intermediate", "3+ years"
    (re.compile(r"\b(?:mid.?level|intermediate|experienced)\b", re.IGNORECASE), "mid"),
]

_EDUCATION_PATTERNS = {
    "phd": re.compile(r"\b(?:ph\.?d|doktor|doctorate|promotion)\b", re.IGNORECASE),
    "master_completed": re.compile(r"\b(?:master(?:'s)?\s+(?:of|in|degree)|m\.?sc\.?|m\.?a\.?\b|master\s+abschluss|masterabschluss)\b", re.IGNORECASE),
    "master_current": re.compile(r"\b(?:master(?:'s)?\s+student|currently\s+(?:pursuing|studying)\s+master|masterstudent|masterstudium)\b", re.IGNORECASE),
    "bachelor": re.compile(r"\b(?:bachelor(?:'s)?(?:\s+(?:of|in|degree))?|b\.?sc\.?|b\.?a\.?\b|bachelorabschluss)\b", re.IGNORECASE),
}


def detect_experience_level(text: str) -> Dict[str, any]:
    """Detect experience level requirements from job description or CV text."""
    result = {
        "level": "unknown",  # entry, mid, senior, unknown
        "min_years": 0,
        "max_years": 0,
        "signals": [],
    }

    for pattern, ptype in _EXPERIENCE_PATTERNS:
        match = pattern.search(text)
        if match:
            if ptype == "years":
                years = int(match.group(1))
                result["min_years"] = years
                result["signals"].append(f"{years}+ years experience")
                if years >= 7:
                    result["level"] = "senior"
                elif years >= 3:
                    result["level"] = "mid"
                else:
                    result["level"] = "entry"
            elif ptype == "range":
                low = int(match.group(1))
                high = int(match.group(2))
                result["min_years"] = low
                result["max_years"] = high
                result["signals"].append(f"{low}-{high} years experience")
                if high >= 7:
                    result["level"] = "senior"
                elif low >= 3:
                    result["level"] = "mid"
                else:
                    result["level"] = "entry"
            elif ptype == "senior":
                result["level"] = "senior"
                result["signals"].append(f"Senior/Lead indicator: '{match.group()}'")
            elif ptype == "entry":
                result["level"] = "entry"
                result["signals"].append(f"Entry/Student indicator: '{match.group()}'")
            elif ptype == "mid":
                result["level"] = "mid"
                result["signals"].append(f"Mid-level indicator: '{match.group()}'")

    return result


def detect_education_level(text: str) -> str:
    """Detect education level from CV text."""
    # Check in order of highest to lowest
    for level, pattern in _EDUCATION_PATTERNS.items():
        if pattern.search(text):
            return level
    return "unknown"


# ─── Profile Extraction from CV ──────────────────────────────────────

def extract_profile_from_cv(cv_data) -> Dict:
    """
    Given a parsed CVData object, extract a rich profile dict.
    This is used during onboarding Step 1.
    """
    raw_text = cv_data.raw_text

    # Extract skills using taxonomy
    extracted_skills = extract_skills_from_text(raw_text, source="cv")

    # Build skill entries for the profile
    skill_entries = []
    for es in extracted_skills:
        skill_entries.append({
            "name": es.canonical,
            "display": es.display,
            "category": es.category,
            "level": "intermediate",  # Default — user can adjust in Step 3
            "years": 0,
            "from_cv": True,
            "confirmed": True,
            "confidence": es.confidence,
        })

    # Detect education
    education_level = detect_education_level(raw_text)

    # Detect experience level
    experience = detect_experience_level(raw_text)

    # Extract contact info
    contact = {
        "name": "",
        "email": cv_data.contact_info.email or "",
        "phone": cv_data.contact_info.phone or "",
        "linkedin": cv_data.contact_info.linkedin or "",
        "github": cv_data.contact_info.github or "",
    }

    # Try to get name from the first non-empty line of text
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    if lines:
        # The first line in most CVs is the person's name
        first_line = lines[0]
        # Heuristic: if it's short and doesn't look like a section heading, it's the name
        if len(first_line) < 60 and not any(
            kw in first_line.lower()
            for kw in ["experience", "education", "skill", "profile", "summary", "curriculum"]
        ):
            contact["name"] = first_line

    return {
        "contact": contact,
        "skills": skill_entries,
        "education_level": education_level,
        "experience_level": experience["level"],
        "experience_signals": experience["signals"],
        "cv_raw_text": raw_text,
        "cv_file_name": cv_data.file_name,
        "sections": list(cv_data.sections.keys()),
    }


# ─── Default Role/Type Suggestions ──────────────────────────────────

# Roles to suggest based on detected skills
ROLE_SUGGESTIONS = {
    "data engineering": ["data_engineering", "etl", "spark", "airflow", "sql", "python"],
    "backend development": ["python", "java", "flask", "django", "fastapi", "spring", "rest api", "node.js"],
    "data science": ["data science", "machine learning", "pandas", "scikit-learn", "numpy", "python"],
    "machine learning engineering": ["machine learning", "deep learning", "pytorch", "tensorflow", "python"],
    "devops / cloud": ["docker", "kubernetes", "aws", "terraform", "ci/cd", "linux"],
    "frontend development": ["react", "angular", "vue", "javascript", "typescript", "html", "css"],
    "full-stack development": ["react", "node.js", "python", "javascript", "sql", "docker"],
    "research / thesis": ["machine learning", "deep learning", "nlp", "computer vision", "pytorch"],
}

JOB_TYPE_OPTIONS = [
    "Werkstudent",
    "Working Student",
    "Internship",
    "Praktikum",
    "Master Thesis",
    "Masterarbeit",
    "Abschlussarbeit",
    "Full-time",
    "Part-time",
    "Freelance",
    "Contract",
]

# Major German cities for location preferences
GERMAN_CITIES = [
    "Berlin", "München", "Hamburg", "Frankfurt am Main", "Köln",
    "Stuttgart", "Düsseldorf", "Leipzig", "Dortmund", "Essen",
    "Bremen", "Dresden", "Hannover", "Nürnberg", "Magdeburg",
    "Wolfsburg", "Braunschweig", "Heidelberg", "Karlsruhe",
    "Mannheim", "Bonn", "Aachen", "Freiburg", "Potsdam",
    "Erlangen", "Darmstadt", "Jena", "Ingolstadt", "Ulm",
    "Regensburg", "Kiel", "Rostock", "Chemnitz", "Kassel",
    "Wiesbaden", "Augsburg", "Bielefeld", "Bochum", "Duisburg",
    "Münster", "Saarbrücken", "Wuppertal", "Würzburg", "Göttingen",
    "Halle", "Erfurt",
]


def suggest_roles(skill_names: List[str]) -> List[Dict[str, any]]:
    """Suggest job roles based on detected skills. Returns role name + match strength."""
    skill_set = set(s.lower() for s in skill_names)
    suggestions = []

    for role, related_skills in ROLE_SUGGESTIONS.items():
        overlap = skill_set & set(related_skills)
        if overlap:
            strength = len(overlap) / len(related_skills)
            suggestions.append({
                "role": role,
                "strength": round(strength, 2),
                "matching_skills": sorted(overlap),
            })

    suggestions.sort(key=lambda x: -x["strength"])
    return suggestions


def build_search_queries_from_profile(profile: Dict) -> Dict[str, List[str]]:
    """
    Generate optimized search queries for scrapers based on user profile.
    This replaces the static `search_keywords` + `cities` from config.yaml.
    
    Returns: {"keywords": [...], "cities": [...], "job_types": [...]}
    """
    keywords = []
    
    # Use desired roles as primary search terms
    desired_roles = profile.get("desired_roles", [])
    if desired_roles:
        keywords.extend(desired_roles)
    
    # Add top skills as secondary keywords
    skills = profile.get("skills", [])
    if isinstance(skills, list):
        # Get confirmed skills sorted by confidence/level
        for skill in skills[:8]:
            name = skill.get("display", skill.get("name", "")) if isinstance(skill, dict) else str(skill)
            if name and name.lower() not in [k.lower() for k in keywords]:
                keywords.append(name)

    # Locations: use profile preferences, fall back to config
    locations = []
    desired_locations = profile.get("desired_locations", [])
    if desired_locations:
        for loc in desired_locations:
            if isinstance(loc, dict):
                locations.append(loc.get("city", ""))
            else:
                locations.append(str(loc))
    
    # Job types from profile
    job_types = profile.get("desired_job_types", [])
    
    # Ensure we always have something to search
    if not keywords:
        keywords = ["Software Engineer", "Data Engineer", "Python Developer"]
    if not locations:
        locations = ["Berlin", "Remote"]
    if not job_types:
        job_types = ["Werkstudent", "Working Student"]

    return {
        "keywords": keywords,
        "cities": [c for c in locations if c],
        "job_types": job_types,
    }
