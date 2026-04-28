#!/usr/bin/env python3
"""
main.py — ATSchecker CLI Entry Point.

Usage:
    python scripts/main.py scan      # Full pipeline: ATS check + scrape jobs + match + HTML report
    python scripts/main.py ats       # Only run ATS checks on PDFs in cvs/ folder
    python scripts/main.py jobs      # Only scrape jobs (no CV analysis)
    python scripts/main.py match     # Match cached jobs against CVs in cvs/ folder
    python scripts/main.py help      # Show usage
"""

import glob
import hashlib
import json
import os
import subprocess
import sys
import webbrowser
from typing import Dict, List

# Add scripts/ to path so imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cv_parser import CVData, parse_cv
from ats_checker import ATSReport, analyze_pdf, run_ats_check
from job_scraper import scrape_all_jobs, get_cached_jobs
from cv_job_matcher import MatchResult, SkillsGapAnalysis, match_cv_to_jobs, analyze_skills_gap
from report_generator import generate_report
from config_state import ensure_config_files, get_effective_config, resolve_cv_skills, update_generated_profile


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_config() -> dict:
    """Load project config."""
    ensure_config_files(PROJECT_ROOT)
    return get_effective_config(PROJECT_ROOT)


def get_cv_folder(config: dict) -> str:
    """Get absolute path to CV folder (profile-specific)."""
    base = os.path.join(PROJECT_ROOT, config.get("paths", {}).get("cv_folder", "cvs"))
    profile_slug = config.get("_active_profile", "")
    if profile_slug:
        return os.path.join(base, profile_slug)
    return base


def get_reports_folder(config: dict) -> str:
    """Get absolute path to reports folder."""
    return os.path.join(PROJECT_ROOT, config.get("paths", {}).get("reports_folder", "reports"))


# ─────────────────────────────────────────────────────────────
# Cache for tracking analyzed files
# ─────────────────────────────────────────────────────────────

def load_analyzed_cache(config: dict) -> Dict:
    """Load the cache of previously analyzed files."""
    cache_path = os.path.join(
        PROJECT_ROOT, config.get("paths", {}).get("analyzed_cache", ".analyzed_cache.json")
    )
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_analyzed_cache(config: dict, cache: Dict):
    """Save the analyzed files cache."""
    cache_path = os.path.join(
        PROJECT_ROOT, config.get("paths", {}).get("analyzed_cache", ".analyzed_cache.json")
    )
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2)


def file_hash(filepath: str) -> str:
    """Compute MD5 hash of a file for change detection."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────
# PDF discovery
# ─────────────────────────────────────────────────────────────

def discover_pdfs(cv_folder: str) -> List[str]:
    """Find all PDF files in the CV folder."""
    pdfs = glob.glob(os.path.join(cv_folder, "*.pdf"))
    pdfs.extend(glob.glob(os.path.join(cv_folder, "*.PDF")))
    return sorted(set(pdfs))


# ─────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────

def cmd_ats(config: dict, force: bool = False):
    """Run ATS checks on all PDFs in the cvs/ folder."""
    cv_folder = get_cv_folder(config)
    os.makedirs(cv_folder, exist_ok=True)

    pdfs = discover_pdfs(cv_folder)
    if not pdfs:
        print(f"\n[!] No PDF files found in {cv_folder}/")
        print(f"  Copy your CV PDFs into the 'cvs/' folder and re-run.")
        return []

    print(f"\nFound {len(pdfs)} PDF(s) in {cv_folder}/")

    # Check cache for unchanged files
    cache = load_analyzed_cache(config) if not force else {}
    reports = []

    for pdf_path in pdfs:
        fname = os.path.basename(pdf_path)
        fhash = file_hash(pdf_path)

        if not force and fname in cache and cache[fname].get("hash") == fhash:
            print(f"  ⏭ {fname} (unchanged, skipping)")
            continue

        print(f"\n  [>] Analyzing: {fname}")
        try:
            report = analyze_pdf(pdf_path)
            reports.append(report)

            # Print summary
            icon = "[OK]" if report.overall_score >= 75 else "[!]" if report.overall_score >= 55 else "[x]"
            print(f"    {icon} ATS Score: {report.overall_score}/100 (Grade: {report.grade})")

            fail_count = sum(1 for c in report.checks if c.status == "fail")
            warn_count = sum(1 for c in report.checks if c.status == "warning")
            if fail_count:
                print(f"    [x] {fail_count} critical issue(s)")
            if warn_count:
                print(f"    [!] {warn_count} warning(s)")

            # Update cache
            cache[fname] = {"hash": fhash, "score": report.overall_score, "grade": report.grade}

        except Exception as e:
            print(f"    [x] Error analyzing {fname}: {e}")

    save_analyzed_cache(config, cache)
    return reports


def cmd_jobs(config: dict, use_cache: bool = False):
    """Scrape jobs from all configured sources."""
    print("\n" + "=" * 60)
    print("JOB SCRAPER — Searching for matching positions")
    print("=" * 60)

    cities = config.get("cities", [])
    print(f"Target cities: {', '.join(cities)}")
    print(f"Job types: {', '.join(config.get('job_types', []))}")

    jobs = scrape_all_jobs(config=config, use_cache=use_cache)

    print(f"\n{'='*60}")
    print(f"Total jobs found: {len(jobs)}")

    # Summary by city
    for city in cities:
        city_count = sum(1 for j in jobs if city.lower() in j.location.lower())
        print(f"  📍 {city}: {city_count} jobs")

    # Summary by source
    sources = {}
    for j in jobs:
        src = j.source.split("→")[0] if "→" in j.source else j.source
        sources[src] = sources.get(src, 0) + 1
    for src, count in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"  🔗 {src}: {count} jobs")

    return jobs


def cmd_match(config: dict):
    """Match CVs against cached/scraped jobs."""
    cv_folder = get_cv_folder(config)
    pdfs = discover_pdfs(cv_folder)

    if not pdfs:
        print(f"\n[!] No PDF files found in {cv_folder}/")
        return [], None

    # Load jobs
    jobs = get_cached_jobs(config=config)
    if not jobs:
        print("\n[!] No cached jobs found. Running job scraper first...")
        jobs = scrape_all_jobs(config=config, use_cache=False)

    if not jobs:
        print("[x] No jobs available for matching.")
        return [], None

    all_matches = []
    all_cv_skills = []

    for pdf_path in pdfs:
        fname = os.path.basename(pdf_path)
        print(f"\n  [>] Matching: {fname}")

        try:
            cv = parse_cv(pdf_path, config=config)
            update_generated_profile(
                PROJECT_ROOT,
                cv_file=os.path.basename(pdf_path),
                cv_skills=cv.skills,
            )
            refreshed = get_config()
            cv_skills = resolve_cv_skills(refreshed, fallback_skills=cv.skills)
            desired_roles = (refreshed.get("opportunity", {}) or {}).get("role_keywords", [])
            matches = match_cv_to_jobs(
                cv=cv,
                jobs=jobs,
                target_cities=refreshed.get("cities", []),
                target_types=refreshed.get("job_types", []),
                current_german_level=str(
                    refreshed.get("german_level")
                    or refreshed.get("matching", {}).get("default_german_level", "A2")
                ),
                config=refreshed,
                cv_skills_override=cv_skills,
                desired_roles=desired_roles,
                experience_level=refreshed.get("experience_level", "entry"),
            )
            all_matches.extend(matches)
            all_cv_skills.extend(cv_skills)

            # Print top 5
            print(f"    Found {len(matches)} matches")
            for i, m in enumerate(matches[:5], 1):
                icon = "🟢" if m.overall_score >= 55 else "🟡" if m.overall_score >= 35 else "🔴"
                print(f"    {icon} {i}. {m.job.title} @ {m.job.company} — {m.overall_score:.1f}%")

        except Exception as e:
            print(f"    [x] Error matching {fname}: {e}")

    # Skills gap analysis
    gap = analyze_skills_gap(all_matches, list(set(all_cv_skills)))

    if gap.top_missing:
        print(f"\n  📊 Top missing skills:")
        for skill in gap.top_missing[:5]:
            print(f"    • {skill} (in {gap.missing_skills_frequency[skill]} jobs)")

    return all_matches, gap


def cmd_scan(config: dict):
    """Full pipeline: ATS check → scrape jobs → match → generate HTML report."""
    print("=" * 60)
    print("ATSchecker — Full Scan")
    print("=" * 60)

    cv_folder = get_cv_folder(config)
    os.makedirs(cv_folder, exist_ok=True)

    pdfs = discover_pdfs(cv_folder)

    # Also check project root for PDFs (in case user placed them there)
    root_pdfs = glob.glob(os.path.join(PROJECT_ROOT, "*.pdf"))
    root_pdfs.extend(glob.glob(os.path.join(PROJECT_ROOT, "*.PDF")))
    if root_pdfs and not pdfs:
        print(f"\n📋 Found {len(root_pdfs)} PDF(s) in project root. Copying to cvs/ folder...")
        import shutil
        for pdf in root_pdfs:
            dest = os.path.join(cv_folder, os.path.basename(pdf))
            if not os.path.exists(dest):
                shutil.copy2(pdf, dest)
                print(f"  → Copied {os.path.basename(pdf)} to cvs/")
        pdfs = discover_pdfs(cv_folder)

    if not pdfs:
        print(f"\n[!] No PDF files found in {cv_folder}/ or project root.")
        print("  Please copy your CV PDFs into the 'cvs/' folder and re-run.")
        return

    # Step 1: ATS Check
    print(f"\n{'─'*60}")
    print("STEP 1: ATS Compatibility Check")
    print(f"{'─'*60}")
    ats_reports = []
    cvs_parsed = []

    for pdf_path in pdfs:
        fname = os.path.basename(pdf_path)
        print(f"\n  [>] Analyzing: {fname}")
        try:
            cv = parse_cv(pdf_path, config=config)
            update_generated_profile(
                PROJECT_ROOT,
                cv_file=os.path.basename(pdf_path),
                cv_skills=cv.skills,
            )
            refreshed = get_config()
            cv_skills = resolve_cv_skills(refreshed, fallback_skills=cv.skills)
            cvs_parsed.append(cv)
            report = run_ats_check(cv)
            ats_reports.append(report)

            icon = "[OK]" if report.overall_score >= 75 else "[!]" if report.overall_score >= 55 else "[x]"
            print(f"    {icon} ATS Score: {report.overall_score}/100 (Grade: {report.grade})")
        except Exception as e:
            print(f"    [x] Error: {e}")

    # Step 2: Scrape Jobs
    print(f"\n{'─'*60}")
    print("STEP 2: Job Scraping")
    print(f"{'─'*60}")

    try:
        jobs = scrape_all_jobs(config=config, use_cache=True)
    except Exception as e:
        print(f"  [x] Job scraping failed: {e}")
        print("  Continuing with any cached jobs...")
        jobs = get_cached_jobs(config=config)

    # Step 3: Match
    print(f"\n{'─'*60}")
    print("STEP 3: CV ↔ Job Matching")
    print(f"{'─'*60}")

    all_matches = []
    all_cv_skills = []

    for cv in cvs_parsed:
        if jobs:
            refreshed = get_config()
            cv_skills = resolve_cv_skills(refreshed, fallback_skills=cv.skills)
            desired_roles = (refreshed.get("opportunity", {}) or {}).get("role_keywords", [])
            matches = match_cv_to_jobs(
                cv=cv,
                jobs=jobs,
                target_cities=refreshed.get("cities", []),
                target_types=refreshed.get("job_types", []),
                current_german_level=str(
                    refreshed.get("german_level")
                    or refreshed.get("matching", {}).get("default_german_level", "A2")
                ),
                config=refreshed,
                cv_skills_override=cv_skills,
                desired_roles=desired_roles,
                experience_level=refreshed.get("experience_level", "entry"),
            )
            all_matches.extend(matches)
        all_cv_skills.extend(cv_skills)

    # Deduplicate matches (same job from multiple CVs)
    seen = set()
    unique_matches = []
    for m in all_matches:
        key = m.job.url
        if key not in seen:
            seen.add(key)
            unique_matches.append(m)
    unique_matches.sort(key=lambda m: m.overall_score, reverse=True)

    # Skills gap
    gap = analyze_skills_gap(unique_matches, list(set(all_cv_skills))) if unique_matches else None

    print(f"\n  Total matches: {len(unique_matches)}")
    high = sum(1 for m in unique_matches if m.overall_score >= 55)
    print(f"  High-likelihood: {high}")

    # Step 4: Generate Report
    print(f"\n{'─'*60}")
    print("STEP 4: Generating HTML Report")
    print(f"{'─'*60}")

    reports_folder = get_reports_folder(config)
    report_path = generate_report(
        cv_reports=ats_reports,
        matches=unique_matches,
        gap_analysis=gap,
        cities=config.get("cities", []),
        output_dir=reports_folder,
    )

    # Open in browser
    print(f"\n{'='*60}")
    print("[OK] SCAN COMPLETE")
    print(f"{'='*60}")
    print(f"  Report: {report_path}")

    try:
        webbrowser.open(f"file://{os.path.abspath(report_path)}")
        print("  📊 Opened report in browser.")
    except Exception:
        print(f"  Open manually: file://{os.path.abspath(report_path)}")


def show_help():
    """Print usage information."""
    print("""
╔══════════════════════════════════════════════════════════╗
║                    ATSchecker                            ║
║     CV Analysis · Job Matching · ATS Compatibility       ║
╚══════════════════════════════════════════════════════════╝

USAGE:
    python scripts/main.py <command>

COMMANDS:
    scan    Full pipeline — ATS check + job scraping + matching + HTML report
    ats     Run ATS compatibility checks only (on PDFs in cvs/ folder)
    jobs    Scrape job listings only (Berlin, Wolfsburg, Leipzig)
    match   Match CVs against cached jobs
    help    Show this help message

WORKFLOW:
    1. Copy your CV PDFs into the 'cvs/' folder
    2. Run: python scripts/main.py scan
    3. View the generated HTML report in reports/ folder

    Next time you update your CV, just drop the new PDF
    into cvs/ and re-run 'scan' — it only processes new/changed files.

CONFIGURATION:
    Edit config.yaml to customize:
    - Target cities and job types
    - Search keywords
    - Scraper priorities and settings
""")


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        show_help()
        sys.exit(0)

    command = sys.argv[1].lower()
    config = get_config()

    if command == "scan":
        cmd_scan(config)
    elif command == "ats":
        reports = cmd_ats(config)
        if reports:
            print(f"\n[OK] Analyzed {len(reports)} CV(s).")
    elif command == "jobs":
        use_cache = "--fresh" not in sys.argv
        jobs = cmd_jobs(config, use_cache=use_cache)
    elif command == "match":
        matches, gap = cmd_match(config)
    elif command in ("help", "--help", "-h"):
        show_help()
    else:
        print(f"Unknown command: {command}")
        show_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
