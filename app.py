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
from job_scraper import load_config, scrape_all_jobs, get_cached_jobs
from cv_job_matcher import match_cv_to_jobs, analyze_skills_gap

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

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

THESIS_MARKERS = [
    "master thesis", "masterarbeit", "master's thesis", "abschlussarbeit",
    "thesis ai", "thesis machine learning", "thesis deep learning",
    "thesis nlp", "thesis data science", "thesis computer vision",
    "thesis reinforcement learning", "diploma thesis", "diplomarbeit",
    "forschungsarbeit", "research thesis",
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_config():
    return load_config(os.path.join(PROJECT_ROOT, "config.yaml"))


def get_cv_folder():
    config = get_config()
    return os.path.join(PROJECT_ROOT, config.get("paths", {}).get("cv_folder", "cvs"))


def is_thesis_job(job):
    text = f"{job.title} {job.description}".lower()
    return any(kw in text for kw in THESIS_MARKERS)


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


def run_ats_on_cv(pdf_path):
    """Parse CV, run ATS, update state. Re-match if jobs exist."""
    cv = parse_cv(pdf_path)
    report = run_ats_check(cv)
    state["cv_data"] = cv
    state["cv_path"] = pdf_path
    state["ats_reports"] = [report]
    if state["jobs"]:
        _run_matching(cv)


def _run_matching(cv):
    config = get_config()
    matches = match_cv_to_jobs(
        cv=cv,
        jobs=state["jobs"],
        target_cities=config.get("cities", []),
        target_types=config.get("job_types", []),
    )
    matches.sort(key=lambda m: m.overall_score, reverse=True)
    state["matches"] = matches
    state["gap_analysis"] = analyze_skills_gap(matches, list(set(cv.skills)))


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
    cv_files = []
    if os.path.exists(cv_folder):
        cv_files = [f for f in os.listdir(cv_folder) if f.lower().endswith(".pdf")]

    all_indexed = list(enumerate(state["jobs"]))
    match_lookup = {m.job.url: m for m in state["matches"]}

    regular = [(i, j) for i, j in all_indexed if not is_thesis_job(j)]
    thesis = [(i, j) for i, j in all_indexed if is_thesis_job(j)]

    def sort_key(item):
        _, job = item
        recent = 0 if is_within_24h(job) else 1
        m = match_lookup.get(job.url)
        score = -(m.overall_score if m else 0)
        return (recent, score)

    regular.sort(key=sort_key)
    thesis.sort(key=sort_key)

    fresh_count = sum(1 for _, j in all_indexed if is_within_24h(j) and not is_thesis_job(j))
    sources = sorted(set(j.source for j in state["jobs"] if j.source))

    return render_template(
        "dashboard.html",
        ats_reports=state["ats_reports"],
        regular_jobs=regular,
        thesis_jobs=thesis,
        match_lookup=match_lookup,
        cv_data=state["cv_data"],
        cv_files=cv_files,
        gap_analysis=state["gap_analysis"],
        config=config,
        all_jobs=state["jobs"],
        is_within_24h=is_within_24h,
        fresh_count=fresh_count,
        sources=sources,
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
                dest = os.path.join(cv_folder, "main.pdf")
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

    dest = os.path.join(cv_folder, f.filename)
    f.save(dest)
    try:
        run_ats_on_cv(dest)
        return jsonify({"status": "success", "message": f"Uploaded and analyzed {f.filename}"})
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

    def _scrape():
        state["scrape_status"] = {"running": True, "message": "Starting scrapers..."}
        state["scrape_logs"] = []
        try:
            config = get_config()
            jobs = scrape_all_jobs(
                config=config,
                use_cache=use_cache,
                cities=selected_cities,
                logger=_log_scrape,
            )
            state["jobs"] = jobs
            if state["cv_data"]:
                state["scrape_status"]["message"] = "Matching jobs to CV..."
                _run_matching(state["cv_data"])
            state["scrape_status"] = {"running": False, "message": f"Done! Found {len(jobs)} jobs."}
        except Exception as e:
            state["scrape_status"] = {"running": False, "message": f"Error: {e}"}

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


if __name__ == "__main__":
    print("=" * 60)
    print("  ATSchecker — Web Dashboard")
    print("=" * 60)
    load_existing_data()
    print(f"\n  Open http://localhost:5000 in your browser\n")
    print("=" * 60)
    app.run(debug=True, port=5000, use_reloader=False)
