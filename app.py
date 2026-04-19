#!/usr/bin/env python3
"""
app.py — ATSchecker Flask Web Application.

Dynamic dashboard for CV analysis, job matching, and AI cover letter generation.

Usage:
    python app.py
    Open http://localhost:5000
"""

import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path


from flask import Flask, render_template, jsonify, request

# Add scripts/ to Python path
SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
sys.path.insert(0, SCRIPT_DIR)

from cv_parser import parse_cv
from ats_checker import run_ats_check
from job_scraper import scrape_all_jobs, get_cached_jobs
from quality_gate import parse_posted_date, summarize_quality_jobs
from scrapers.base import is_listing_page
from cv_job_matcher import match_cv_to_jobs, analyze_skills_gap
from config_state import (
    ensure_config_files,
    get_effective_config,
    get_cv_filename,
    get_upload_max_size_bytes,
    resolve_cv_skills,
    update_generated_profile,
)


app = Flask(__name__)

# ─── Jinja2 Filter for Date Formatting ─────────────────────────────

def format_date(value):
    """Format a date string, timestamp, or datetime object as yyyy mm dd HH:MM:SS. Logs unparseable values for debugging."""
    import sys
    if not value:
        return "N/A"
    try:
        dt = parse_posted_date(value)
        if not dt:
            print(f"[format_date] Could not parse posted_date: {value}", file=sys.stderr)
            return str(value)
        return dt.strftime("%Y %m %d %H:%M:%S")
    except Exception as e:
        print(f"[format_date] Error parsing posted_date: {value} ({e})", file=sys.stderr)
        return str(value)

app.jinja_env.filters['format_date'] = format_date

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ensure_config_files(PROJECT_ROOT)
app.config["MAX_CONTENT_LENGTH"] = get_upload_max_size_bytes(get_effective_config(PROJECT_ROOT))

# ─── Global Application State ─────────────────────────────────────────────────

state = {
    "jobs": [],
    "matches": [],
    "ats_reports": [],
    "cv_data": None,
    "cv_path": None,
    "gap_analysis": None,
    "scrape_status": {"running": False, "message": ""},
    "compile_status": {"running": False, "message": ""},
    "scrape_logs": [],
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_config():
    return get_effective_config(PROJECT_ROOT)


def get_cv_folder():
    config = get_config()
    return os.path.join(PROJECT_ROOT, config.get("paths", {}).get("cv_folder", "cvs"))


def is_thesis_job(job):
    config = get_config()
    thesis_markers = config.get("matching", {}).get("thesis_markers", [])
    if not isinstance(thesis_markers, list):
        thesis_markers = []
    text = f"{job.title} {job.description}".lower()
    return any(str(kw).lower() in text for kw in thesis_markers if str(kw).strip())


def is_within_24h(job):
    if not job.posted_date:
        return False
    try:
        dt = datetime.fromisoformat(str(job.posted_date).replace("Z", "+00:00"))
        return (datetime.now() - dt.replace(tzinfo=None)) < timedelta(hours=24)
    except (ValueError, TypeError, AttributeError):
        pass
    pd = str(job.posted_date).lower()
    if any(w in pd for w in ["just now", "moment", "gerade", "jetzt"]):
        return True
    if any(w in pd for w in ["hour", "stunde", "minute", "min ago", "min."]):
        return True
    if any(w in pd for w in ["today", "heute", "1 day", "1 tag"]):
        return True
    return False


def _to_lower_list(values, fallback):
    source = values if isinstance(values, list) and values else fallback
    return [str(v).strip().lower() for v in source if str(v).strip()]


def _get_opportunity_config(config: dict):
    opportunity_cfg = config.get("opportunity", {}) if config else {}
    return {
        "precious_min_score": int(opportunity_cfg.get("precious_min_score", 5)),
        "recency_hours": int(opportunity_cfg.get("recency_hours", 72)),
        "role_keywords": _to_lower_list(
            opportunity_cfg.get("role_keywords", []),
            [],
        ),
        "prestige_companies": _to_lower_list(
            opportunity_cfg.get("prestige_companies", []),
            [],
        ),
        "location_keywords": _to_lower_list(
            opportunity_cfg.get("location_keywords", []),
            [],
        ),
    }


def _job_text(job):
    parts = [
        job.title,
        job.description,
        job.company,
        job.location,
        job.source,
        job.job_type,
        " ".join(job.tags or []),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _is_recent_within_hours(job, hours: int):
    if hours <= 24:
        return is_within_24h(job)

    val = job.posted_date
    if not val:
        return False

    try:
        if isinstance(val, int) or (isinstance(val, str) and val.isdigit()):
            dt = datetime.fromtimestamp(int(val))
        elif isinstance(val, str):
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        else:
            dt = val
        return (datetime.now() - dt.replace(tzinfo=None)) < timedelta(hours=hours)
    except Exception:
        return is_within_24h(job)


def classify_opportunity(job, config: dict):
    cfg = _get_opportunity_config(config)
    text = _job_text(job)

    score = 0
    reasons = []

    is_thesis = is_thesis_job(job)
    if is_thesis:
        score += 5
        reasons.append("Thesis/Research")

    role_hit = any(k in text for k in cfg["role_keywords"])
    if role_hit:
        score += 3
        reasons.append("DS/ML/Engineering role")

    company_text = str(job.company or "").lower()
    if any(c in company_text for c in cfg["prestige_companies"]):
        score += 3
        reasons.append("High-impact company")

    if _is_recent_within_hours(job, cfg["recency_hours"]):
        score += 2
        reasons.append("Recently posted")

    loc_text = f"{job.location} {job.title}".lower()
    if any(k in loc_text for k in cfg["location_keywords"]):
        score += 1
        reasons.append("Priority location")

    is_precious = is_thesis or score >= cfg["precious_min_score"]

    return {
        "score": score,
        "reasons": reasons,
        "thesis": is_thesis,
        "interesting": role_hit,
        "precious": is_precious,
    }


def _match_score(job, match_lookup: dict) -> float:
    match = match_lookup.get(job.url)
    return float(match.overall_score if match else 0)


def _quality_score(job) -> int:
    return int((job.quality or {}).get("score", 0))


def _posted_timestamp(job) -> float:
    dt = parse_posted_date(job.posted_date)
    return dt.timestamp() if dt else 0.0


def _display_sort_key(job, match_lookup: dict, opportunity_lookup: dict):
    opportunity = opportunity_lookup.get(job.url, {})
    recent_rank = 0 if is_within_24h(job) else 1
    return (
        recent_rank,
        -_match_score(job, match_lookup),
        -int(opportunity.get("score", 0)),
        -_quality_score(job),
        -_posted_timestamp(job),
        str(job.title or "").lower(),
    )


def _group_label(job, group_by: str, match_lookup: dict, opportunity_lookup: dict):
    if group_by == "company":
        return job.company or "Unknown Company"
    if group_by == "source":
        return job.source or "Unknown Source"
    if group_by == "match-tier":
        match_score = _match_score(job, match_lookup)
        opportunity = opportunity_lookup.get(job.url, {})
        if match_score >= 80:
            return "Excellent Match (80%+)"
        if match_score >= 60:
            return "Strong Match (60-79%)"
        if match_score >= 40:
            return "Potential Match (40-59%)"
        if opportunity.get("precious") or opportunity.get("interesting"):
            return "Discovery Candidates"
        return "Needs Review"

    dt = parse_posted_date(job.posted_date)
    return dt.strftime("%Y %m %d") if dt else "N/A"


def _group_sort_key(label: str, group_by: str):
    if group_by == "date":
        dt = parse_posted_date(label)
        return (0, -(dt.timestamp() if dt else 0))
    if group_by == "match-tier":
        order = {
            "Excellent Match (80%+)": 0,
            "Strong Match (60-79%)": 1,
            "Potential Match (40-59%)": 2,
            "Discovery Candidates": 3,
            "Needs Review": 4,
        }
        return (0, order.get(label, 99))
    return (0, label.lower())


def build_grouped_jobs(indexed_jobs, group_by: str, match_lookup: dict, opportunity_lookup: dict):
    groups = {}
    for job_id, job in indexed_jobs:
        label = _group_label(job, group_by, match_lookup, opportunity_lookup)
        groups.setdefault(label, []).append((job_id, job))

    grouped = []
    for label, jobs in groups.items():
        grouped.append({
            "label": label,
            "jobs": sorted(jobs, key=lambda item: _display_sort_key(item[1], match_lookup, opportunity_lookup)),
        })

    grouped.sort(key=lambda item: _group_sort_key(item["label"], group_by))
    return grouped


def run_ats_on_cv(pdf_path):
    """Parse CV, run ATS, update state. Re-match if jobs exist."""
    config = get_config()
    cv = parse_cv(pdf_path, config=config)
    report = run_ats_check(cv)
    update_generated_profile(
        PROJECT_ROOT,
        cv_file=os.path.basename(pdf_path),
        cv_skills=cv.skills,
    )
    state["cv_data"] = cv
    state["cv_path"] = pdf_path
    state["ats_reports"] = [report]
    if state["jobs"]:
        _run_matching(cv)


def _run_matching(cv):
    config = get_config()
    cv_skills = resolve_cv_skills(config, fallback_skills=cv.skills)
    language_level = str(config.get("matching", {}).get("default_german_level", "A2"))
    matches = match_cv_to_jobs(
        cv=cv,
        jobs=state["jobs"],
        target_cities=config.get("cities", []),
        target_types=config.get("job_types", []),
        current_german_level=language_level,
        config=config,
        cv_skills_override=cv_skills,
    )
    matches.sort(key=lambda m: m.overall_score, reverse=True)
    state["matches"] = matches
    state["gap_analysis"] = analyze_skills_gap(matches, list(dict.fromkeys(cv_skills)))


def _log_scrape(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {msg}"
    state["scrape_logs"].append(entry)
    # Keep last 300 lines to avoid unbounded growth
    if len(state["scrape_logs"]) > 300:
        state["scrape_logs"] = state["scrape_logs"][-300:]
    print(entry)


# ─── Context Processor ───────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    return {
        "cv_loaded": state["cv_data"] is not None,
        "job_count": len(state["jobs"]),
    }


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    config = get_config()
    cv_folder = get_cv_folder()
    group_options = [
        ("match-tier", "Match Tier"),
        ("date", "Date"),
        ("company", "Company"),
        ("source", "Source"),
    ]
    selected_group_by = request.args.get(
        "group",
        config.get("dashboard", {}).get("default_group_by", "match-tier"),
    )
    if selected_group_by not in {key for key, _ in group_options}:
        selected_group_by = config.get("dashboard", {}).get("default_group_by", "match-tier")

    cv_files = []
    if os.path.exists(cv_folder):
        cv_files = [f for f in os.listdir(cv_folder) if f.lower().endswith(".pdf")]

    all_indexed = list(enumerate(state["jobs"]))
    match_lookup = {m.job.url: m for m in state["matches"]}
    opportunity_lookup = {
        j.url: classify_opportunity(j, config)
        for _, j in all_indexed
    }

    regular = [(i, j) for i, j in all_indexed if not is_thesis_job(j)]
    regular.sort(key=lambda item: _display_sort_key(item[1], match_lookup, opportunity_lookup))
    grouped_jobs = build_grouped_jobs(regular, selected_group_by, match_lookup, opportunity_lookup)

    thesis = [(i, j) for i, j in all_indexed if is_thesis_job(j)]

    interesting = [
        (i, j)
        for i, j in all_indexed
        if opportunity_lookup.get(j.url, {}).get("interesting", False)
    ]

    precious = [
        (i, j)
        for i, j in all_indexed
        if opportunity_lookup.get(j.url, {}).get("precious", False)
    ]

    def sort_key(item):
        _, job = item
        return _display_sort_key(job, match_lookup, opportunity_lookup)

    thesis.sort(key=sort_key)
    interesting.sort(key=sort_key)
    precious.sort(key=sort_key)

    fresh_count = sum(1 for _, j in all_indexed if is_within_24h(j) and not is_thesis_job(j))
    interesting_count = len(interesting)
    precious_count = len(precious)
    sources = sorted(set(j.source for j in state["jobs"] if j.source))
    quality_summary = summarize_quality_jobs(state["jobs"])

    return render_template(
        "dashboard.html",
        ats_reports=state["ats_reports"],
        regular_jobs=regular,
        thesis_jobs=thesis,
        interesting_jobs=interesting,
        precious_jobs=precious,
        match_lookup=match_lookup,
        opportunity_lookup=opportunity_lookup,
        cv_data=state["cv_data"],
        cv_files=cv_files,
        gap_analysis=state["gap_analysis"],
        config=config,
        all_jobs=state["jobs"],
        is_within_24h=is_within_24h,
        fresh_count=fresh_count,
        interesting_count=interesting_count,
        precious_count=precious_count,
        sources=sources,
        grouped_jobs=grouped_jobs,
        group_options=group_options,
        selected_group_by=selected_group_by,
        quality_summary=quality_summary,
    )


@app.route("/job/<int:job_id>")
def job_detail(job_id):
    if job_id < 0 or job_id >= len(state["jobs"]):
        return "Job not found", 404
    job = state["jobs"][job_id]
    match_info = None
    for m in state["matches"]:
        if m.job.url == job.url:
            match_info = m
            break
    return render_template(
        "job_detail.html",
        job=job,
        job_id=job_id,
        match_info=match_info,
        opportunity_info=classify_opportunity(job, get_config()),
        quality_info=job.quality or {},
        cv_data=state["cv_data"],
        is_recent=is_within_24h(job),
        is_thesis=is_thesis_job(job),
    )


@app.route("/api/compile", methods=["POST"])
def compile_cv():
    """Compile main.tex -> PDF, save to cvs/, run ATS."""
    if state["compile_status"]["running"]:
        return jsonify({"error": "Already compiling"}), 409

    def _compile():
        state["compile_status"] = {"running": True, "message": "Compiling LaTeX..."}
        try:
            tex_dir = os.path.join(PROJECT_ROOT, "new_CV_copilot")
            tex_path = os.path.join(tex_dir, "main.tex")
            if not os.path.exists(tex_path):
                state["compile_status"] = {"running": False, "message": "Error: main.tex not found"}
                return

            # Sanitize
            sanitizer = os.path.join(SCRIPT_DIR, "sanitize_tex.py")
            if os.path.exists(sanitizer):
                subprocess.run([sys.executable, sanitizer, tex_path], capture_output=True)

            compiler = None
            for cmd in ("pdflatex", "xelatex", "lualatex"):
                if shutil.which(cmd):
                    compiler = cmd
                    break
            if not compiler:
                state["compile_status"] = {"running": False, "message": "No LaTeX compiler found on PATH"}
                return

            for i in range(2):
                state["compile_status"]["message"] = f"Running {compiler} (pass {i + 1}/2)..."
                r = subprocess.run(
                    [compiler, "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                    cwd=tex_dir, capture_output=True, text=True,
                )
                if r.returncode != 0:
                    state["compile_status"] = {"running": False, "message": "Compilation failed"}
                    return

            cv_folder = get_cv_folder()
            os.makedirs(cv_folder, exist_ok=True)
            for old in Path(cv_folder).glob("*.pdf"):
                old.unlink()

            src = os.path.join(tex_dir, "main.pdf")
            if os.path.exists(src):
                cfg = get_config()
                dest = os.path.join(cv_folder, get_cv_filename(cfg, source="compile"))
                shutil.copy2(src, dest)
                state["compile_status"]["message"] = "Running ATS check..."
                run_ats_on_cv(dest)
                state["compile_status"] = {"running": False, "message": "Compiled and analyzed successfully!"}
            else:
                state["compile_status"] = {"running": False, "message": "PDF not generated"}
        except Exception as e:
            state["compile_status"] = {"running": False, "message": f"Error: {e}"}

    threading.Thread(target=_compile, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/upload", methods=["POST"])
def upload_cv():
    """Upload a PDF file to cvs/ and run ATS."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files accepted"}), 400

    cv_folder = get_cv_folder()
    os.makedirs(cv_folder, exist_ok=True)
    for old in Path(cv_folder).glob("*.pdf"):
        old.unlink()

    cfg = get_config()
    dest = os.path.join(cv_folder, get_cv_filename(cfg, source="upload"))
    f.save(dest)
    try:
        run_ats_on_cv(dest)
        return jsonify({"status": "success", "message": f"Uploaded and analyzed {os.path.basename(dest)}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scrape", methods=["POST"])
def start_scrape():
    """Start background job scraping."""
    if state["scrape_status"]["running"]:
        return jsonify({"error": "Scraping already running"}), 409
    use_cache = request.json.get("use_cache", False) if request.is_json else False
    selected_cities = None
    if request.is_json:
        selected_cities = request.json.get("cities") or None

    def _on_batch(new_jobs):
        """Called after each city scrape — push jobs into state immediately, deduped and filtered."""
        for job in new_jobs:
            # Filter out search listing pages
            if is_listing_page(job.title, job.url):
                continue
            url_key = (job.quality or {}).get("normalized_url") or job.url
            if not any(((j.quality or {}).get("normalized_url") or j.url) == url_key for j in state["jobs"]):
                state["jobs"].append(job)


    def _scrape():
        state["scrape_status"] = {"running": True, "message": "Starting scrapers..."}
        state["scrape_logs"] = []
        # Clear all previous jobs so only new jobs are shown
        state["jobs"] = []

        try:
            config = get_config()
            jobs = scrape_all_jobs(
                config=config,
                use_cache=use_cache,
                cities=selected_cities,
                logger=_log_scrape,
                on_batch=_on_batch,
            )
            # Replace state with final verified+deduplicated jobs from scrape_all_jobs
            state["jobs"] = jobs
            state["scrape_status"] = {"running": False, "message": f"Done! {len(state['jobs'])} verified jobs available. Click 'Sort & Analyze' to rank them."}
        except Exception as e:
            # Even on error, keep whatever jobs were found so far
            state["scrape_status"] = {"running": False, "message": f"Stopped: {e}. {len(state['jobs'])} jobs kept."}

    threading.Thread(target=_scrape, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/status")
def get_status():
    """Poll endpoint for background task progress."""
    return jsonify({
        "scrape": state["scrape_status"],
        "compile": state["compile_status"],
        "job_count": len(state["jobs"]),
        "cv_loaded": state["cv_data"] is not None,
        "ats_score": state["ats_reports"][0].overall_score if state["ats_reports"] else None,
    })


@app.route("/api/scrape/logs")
def get_scrape_logs():
    return jsonify({"logs": state.get("scrape_logs", [])})


@app.route("/api/jobs")
def get_jobs_live():
    """Return current jobs list sorted: recent postings first, then by match score."""
    config = get_config()
    match_lookup = {m.job.url: m for m in state["matches"]}
    indexed = list(enumerate(state["jobs"]))
    quality_summary = summarize_quality_jobs(state["jobs"])

    def _sort_key(item):
        _, job = item
        opportunity = classify_opportunity(job, config)
        recent = 0 if is_within_24h(job) else 1
        score = -_match_score(job, match_lookup)
        return (recent, -int(opportunity.get("score", 0)), -_quality_score(job), score, -_posted_timestamp(job))

    indexed.sort(key=_sort_key)

    jobs_data = []
    for orig_idx, job in indexed:
        m = match_lookup.get(job.url)
        opportunity = classify_opportunity(job, config)
        jobs_data.append({
            "id": orig_idx,
            "title": job.title[:65],
            "company": job.company[:35] if job.company else "",
            "location": job.location[:25] if job.location else "",
            "source": job.source or "",
            "posted_date": str(job.posted_date or "N/A"),
            "match": round(m.overall_score, 1) if m else 0,
            "recent": is_within_24h(job),
            "interesting": opportunity.get("interesting", False),
            "precious": opportunity.get("precious", False),
            "opportunity_score": opportunity.get("score", 0),
            "opportunity_reasons": opportunity.get("reasons", []),
            "quality_score": _quality_score(job),
            "quality_bucket": (job.quality or {}).get("bucket", "low"),
            "quality_flags": (job.quality or {}).get("flags", []),
            "url_status": (job.quality or {}).get("url_status", "unknown"),
        })
    return jsonify({
        "jobs": jobs_data,
        "total": len(jobs_data),
        "scraping": state["scrape_status"]["running"],
        "quality": quality_summary,
    })


@app.route("/api/analyze-jobs", methods=["POST"])
def analyze_jobs():
    """Run CV matching and sort jobs by likelihood + recency. Called after scraping."""
    if not state["cv_data"]:
        return jsonify({"error": "No CV loaded. Upload or compile your CV first."}), 400
    if not state["jobs"]:
        return jsonify({"error": "No jobs to analyze."}), 400

    try:
        _run_matching(state["cv_data"])
        # Save updated cache
        from job_scraper import save_cache
        config = get_config()
        cache_path = os.path.join(
            PROJECT_ROOT,
            config.get("paths", {}).get("cache_file", ".job_cache.json"),
        )
        save_cache(cache_path, state["jobs"])
        return jsonify({
            "status": "success",
            "message": f"Analyzed {len(state['jobs'])} jobs. Sorted by match score + recency.",
            "job_count": len(state["jobs"]),
            "match_count": len(state["matches"]),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    """Gracefully shut down the Flask server to free the port."""
    func = request.environ.get('werkzeug.server.shutdown')
    if func:
        func()
    else:
        # For newer Werkzeug versions, use os._exit in a thread
        def _shutdown():
            import time
            time.sleep(0.5)
            os._exit(0)
        threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({"status": "shutting_down", "message": "Server shutting down... port will be freed."})


@app.route("/api/analyze/<int:job_id>", methods=["POST"])
def analyze_job(job_id):
    """On-demand Ollama analysis for cover letter — only runs when user clicks."""
    if job_id < 0 or job_id >= len(state["jobs"]):
        return jsonify({"error": "Job not found"}), 404
    if not state["cv_data"]:
        return jsonify({"error": "No CV loaded. Compile or upload a CV first."}), 400

    job = state["jobs"][job_id]
    try:
        from ollama_analyzer import analyze_for_cover_letter, check_ollama_available
        if not check_ollama_available():
            return jsonify({
                "error": "Ollama is not running or llama3.1:8b model not found. "
                         "Start Ollama first: ollama serve && ollama pull llama3.1:8b"
            }), 503
        result = analyze_for_cover_letter(
            state["cv_data"].raw_text,
            job.title, job.company, job.location, job.description,
        )
        return jsonify(result)
    except ImportError:
        return jsonify({"error": "ollama package not installed. Run: pip install ollama"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ats")
def get_ats_report():
    if not state["ats_reports"]:
        return jsonify({"error": "No ATS report available"}), 404
    r = state["ats_reports"][0]
    return jsonify({
        "score": r.overall_score,
        "grade": r.grade,
        "checks": [
            {
                "name": c.name, "score": c.score, "max_score": c.max_score,
                "status": c.status, "message": c.message, "details": c.details,
            }
            for c in r.checks
        ],
        "recommendations": r.recommendations,
    })


# ─── Startup ─────────────────────────────────────────────────────────────────

def load_existing_data():
    """Load cached jobs and existing CV on startup (no auto-compile)."""
    config = get_config()
    cv_folder = get_cv_folder()

    # Load cached jobs
    try:
        jobs = get_cached_jobs(config=config)
        if jobs:
            state["jobs"] = jobs
            print(f"  Loaded {len(jobs)} cached jobs")
    except Exception:
        pass

    # Load existing CV PDF (if any)
    if os.path.exists(cv_folder):
        pdfs = [f for f in os.listdir(cv_folder) if f.lower().endswith(".pdf")]
        if pdfs:
            pdf_path = os.path.join(cv_folder, pdfs[0])
            try:
                run_ats_on_cv(pdf_path)
                print(f"  Loaded CV: {pdfs[0]} (ATS: {state['ats_reports'][0].overall_score}/100)")
            except Exception as e:
                print(f"  Warning: Could not load CV: {e}")


def find_free_port(start=5001, end=5020):
    """Find the first free port in a range."""
    import socket
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start


if __name__ == "__main__":
    print("=" * 60)
    print("  ATSchecker — Web Dashboard")
    print("=" * 60)
    load_existing_data()
    port = find_free_port()
    print(f"\n  Open http://localhost:{port} in your browser")
    print(f"  (Use the Quit button in the navbar to safely stop the server)\n")
    print("=" * 60)
    app.run(debug=False, host="127.0.0.1", port=port, use_reloader=False)
