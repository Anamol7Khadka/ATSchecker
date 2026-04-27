"""Configuration bootstrap and generated-state helpers.

This module keeps user-authored config.yaml immutable during CV uploads by
storing derived profile data in config.generated.yaml.

Supports multi-profile architecture: individual YAML files in profiles/
directory, with .active_profile tracking the current user.
"""

import glob
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CONFIG: Dict[str, Any] = {
    "cities": ["Berlin", "Wolfsburg", "Leipzig"],
    "job_types": ["Werkstudent", "Working Student", "Internship", "Praktikum"],
    "search_keywords": ["Data Engineering", "Backend Development", "Software Engineer"],
    "cv_skills": [],
    "scraping": {},
    "opportunity": {},
    "quality": {},
    "dashboard": {"default_group_by": "match-tier"},
    "ats": {},
    "paths": {
        "cv_folder": "cvs",
        "reports_folder": "reports",
        "cache_file": ".job_cache.json",
        "analyzed_cache": ".analyzed_cache.json",
    },
    "behavior": {
        "max_upload_size_mb": 16,
        "compiled_cv_filename": "compiled_cv.pdf",
        "uploaded_cv_filename": "uploaded_cv.pdf",
    },
    "matching": {
        "default_german_level": "A2",
        "language_level_map": {"a1": 1, "a2": 2, "b1": 3, "b2": 4, "c1": 5, "c2": 6},
        "german_patterns": [],
        "preferred_german_phrases": ["german preferred", "german is a plus", "deutsch von vorteil"],
        "tech_keywords": [],
        "cv_parser_skills": [],
        "thesis_markers": [],
    },
    "profile": {
        "cv_skills_override": [],
    },
}

DEFAULT_GENERATED: Dict[str, Any] = {
    "profile": {
        "cv_file": "",
        "cv_skills_extracted": [],
        "updated_at": None,
    }
}


def _load_yaml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_yaml(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=False)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _paths(project_root: str) -> Dict[str, str]:
    return {
        "template": os.path.join(project_root, "config.template.yaml"),
        "config": os.path.join(project_root, "config.yaml"),
        "generated": os.path.join(project_root, "config.generated.yaml"),
        "profiles_dir": os.path.join(project_root, "profiles"),
        "active_profile": os.path.join(project_root, ".active_profile"),
    }


def ensure_config_files(project_root: str) -> Dict[str, Any]:
    paths = _paths(project_root)
    created_config = False
    created_generated = False

    if not os.path.exists(paths["config"]):
        if os.path.exists(paths["template"]):
            shutil.copy2(paths["template"], paths["config"])
        else:
            _write_yaml(paths["config"], DEFAULT_CONFIG)
        created_config = True

    if not os.path.exists(paths["generated"]):
        _write_yaml(paths["generated"], DEFAULT_GENERATED)
        created_generated = True

    return {
        "paths": paths,
        "created_config": created_config,
        "created_generated": created_generated,
    }


def get_effective_config(project_root: str) -> Dict[str, Any]:
    info = ensure_config_files(project_root)
    paths = info["paths"]

    template_data = _load_yaml(paths["template"]) if os.path.exists(paths["template"]) else DEFAULT_CONFIG
    generated_data = _load_yaml(paths["generated"]) or DEFAULT_GENERATED
    user_data = _load_yaml(paths["config"]) or {}

    # Load active profile YAML (person-specific overrides)
    profile_data = {}
    active_name = get_active_profile_name(project_root)
    if active_name:
        profile_path = os.path.join(paths["profiles_dir"], f"{active_name}.yaml")
        profile_data = _load_yaml(profile_path)

    # Merge chain: template → generated → shared config → active profile
    merged = _deep_merge(template_data, generated_data)
    merged = _deep_merge(merged, user_data)
    if profile_data:
        merged = _deep_merge(merged, profile_data)
        # Also store the active profile name for downstream consumers
        merged["_active_profile"] = active_name

        # Make cache paths profile-specific so each user has isolated data
        cfg_paths = merged.setdefault("paths", {})
        for key in ("cache_file", "analyzed_cache"):
            original = cfg_paths.get(key, "")
            if original and active_name and f".{active_name}." not in original:
                base, ext = os.path.splitext(original)
                cfg_paths[key] = f"{base}.{active_name}{ext}"

    return merged


def update_generated_profile(
    project_root: str,
    cv_file: Optional[str] = None,
    cv_skills: Optional[List[str]] = None,
) -> Dict[str, Any]:
    info = ensure_config_files(project_root)
    generated_path = info["paths"]["generated"]

    data = _load_yaml(generated_path)
    profile = data.setdefault("profile", {})

    if cv_file is not None:
        profile["cv_file"] = cv_file

    if cv_skills is not None:
        deduped = list(dict.fromkeys([str(skill).strip() for skill in cv_skills if str(skill).strip()]))
        profile["cv_skills_extracted"] = deduped

    profile["updated_at"] = datetime.utcnow().isoformat() + "Z"

    _write_yaml(generated_path, data)
    return data


def resolve_cv_skills(config: Dict[str, Any], fallback_skills: Optional[List[str]] = None) -> List[str]:
    profile = config.get("profile", {}) if isinstance(config, dict) else {}

    override = profile.get("cv_skills_override", []) if isinstance(profile, dict) else []
    extracted = profile.get("cv_skills_extracted", []) if isinstance(profile, dict) else []
    legacy = config.get("cv_skills", []) if isinstance(config, dict) else []

    if isinstance(override, list) and override:
        selected = override
    elif isinstance(extracted, list) and extracted:
        selected = extracted
    elif isinstance(legacy, list) and legacy:
        selected = legacy
    else:
        selected = fallback_skills or []

    return list(dict.fromkeys([str(skill).strip() for skill in selected if str(skill).strip()]))


def get_cv_filename(config: Dict[str, Any], source: str) -> str:
    behavior = config.get("behavior", {}) if isinstance(config, dict) else {}
    if source == "compile":
        return str(behavior.get("compiled_cv_filename", "compiled_cv.pdf"))
    return str(behavior.get("uploaded_cv_filename", "uploaded_cv.pdf"))


def get_upload_max_size_bytes(config: Dict[str, Any]) -> int:
    behavior = config.get("behavior", {}) if isinstance(config, dict) else {}
    mb = int(behavior.get("max_upload_size_mb", 16))
    return max(1, mb) * 1024 * 1024


# ── Multi-Profile Management ──────────────────────────────────────────

def list_profiles(project_root: str) -> List[Dict[str, Any]]:
    """Scan profiles/ directory and return metadata for each profile YAML."""
    profiles_dir = os.path.join(project_root, "profiles")
    if not os.path.isdir(profiles_dir):
        return []

    active = get_active_profile_name(project_root)
    profiles = []

    for path in sorted(glob.glob(os.path.join(profiles_dir, "*.yaml"))):
        fname = os.path.basename(path)
        if fname.startswith("_"):
            continue  # skip _template.yaml
        slug = fname.rsplit(".", 1)[0]  # "aakash_khadka"
        data = _load_yaml(path)
        profiles.append({
            "slug": slug,
            "name": data.get("name", slug.replace("_", " ").title()),
            "german_level": data.get("german_level", ""),
            "cities": data.get("cities", []),
            "file": fname,
            "is_active": slug == active,
        })

    return profiles


def get_active_profile_name(project_root: str) -> Optional[str]:
    """Read .active_profile file. Returns slug or None."""
    active_path = os.path.join(project_root, ".active_profile")
    if os.path.exists(active_path):
        try:
            with open(active_path, "r", encoding="utf-8") as f:
                name = f.read().strip()
                if name:
                    return name
        except Exception:
            pass

    # Auto-detect: use the first profile YAML found
    profiles_dir = os.path.join(project_root, "profiles")
    if os.path.isdir(profiles_dir):
        yamls = sorted(
            f for f in os.listdir(profiles_dir)
            if f.endswith(".yaml") and not f.startswith("_")
        )
        if yamls:
            slug = yamls[0].rsplit(".", 1)[0]
            set_active_profile(project_root, slug)
            return slug

    return None


def set_active_profile(project_root: str, profile_slug: str) -> bool:
    """Write .active_profile file. Returns True on success."""
    profiles_dir = os.path.join(project_root, "profiles")
    profile_path = os.path.join(profiles_dir, f"{profile_slug}.yaml")

    if not os.path.exists(profile_path):
        return False

    active_path = os.path.join(project_root, ".active_profile")
    try:
        with open(active_path, "w", encoding="utf-8") as f:
            f.write(profile_slug)
        return True
    except Exception:
        return False
