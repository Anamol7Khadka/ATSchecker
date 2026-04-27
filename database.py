"""
database.py — SQLite persistent storage for ATSchecker.

Replaces the in-memory global `state = {}` dict.  All data survives
server restarts.  The schema is auto-created on first run and
migrated forward non-destructively when columns are added.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

# One connection per thread (SQLite requirement)
_local = threading.local()

DB_NAME = "atschecker.db"

# Active profile slug — set by app.py on startup and profile switch
_active_profile = ""

def set_active_profile(slug: str):
    """Set the active profile slug for all subsequent DB queries."""
    global _active_profile
    _active_profile = slug or ""

def get_active_profile() -> str:
    return _active_profile


def _db_path() -> str:
    """Return absolute path to the database file (project root)."""
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        DB_NAME,
    )


def get_connection() -> sqlite3.Connection:
    """Get or create a per-thread SQLite connection."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_db_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


@contextmanager
def transaction():
    """Context manager that commits on success, rolls back on error."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ─── Schema ──────────────────────────────────────────────────────────

SCHEMA_VERSION = 1

_SCHEMA_SQL = """
-- Tracks schema version for future migrations
CREATE TABLE IF NOT EXISTS schema_info (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- User profile (single-user app — one row)
CREATE TABLE IF NOT EXISTS user_profile (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT DEFAULT '',
    email               TEXT DEFAULT '',
    phone               TEXT DEFAULT '',
    linkedin            TEXT DEFAULT '',
    github              TEXT DEFAULT '',
    education_level     TEXT DEFAULT '',
    field_of_study      TEXT DEFAULT '',
    graduation_date     TEXT DEFAULT '',
    german_level        TEXT DEFAULT 'A2',
    available_from      TEXT DEFAULT '',
    hours_per_week      INTEGER DEFAULT 20,
    salary_expectation  TEXT DEFAULT '',
    desired_roles       TEXT DEFAULT '[]',
    desired_job_types   TEXT DEFAULT '[]',
    desired_locations   TEXT DEFAULT '[]',
    desired_companies   TEXT DEFAULT '[]',
    min_match_threshold INTEGER DEFAULT 30,
    cv_raw_text         TEXT DEFAULT '',
    cv_file_name        TEXT DEFAULT '',
    cv_uploaded_at      TEXT DEFAULT '',
    ats_score           INTEGER DEFAULT 0,
    ats_grade           TEXT DEFAULT '',
    ats_report_json     TEXT DEFAULT '{}',
    onboarding_complete INTEGER DEFAULT 0,
    profile_slug        TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- Skills — one row per skill, linked to profile
CREATE TABLE IF NOT EXISTS user_skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    category    TEXT DEFAULT '',
    level       TEXT DEFAULT 'intermediate',
    years       REAL DEFAULT 0,
    from_cv     INTEGER DEFAULT 1,
    confirmed   INTEGER DEFAULT 1,
    UNIQUE(name)
);

-- Jobs — scraped job postings, persisted
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    company         TEXT DEFAULT '',
    location        TEXT DEFAULT '',
    url             TEXT NOT NULL,
    normalized_url  TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    posted_date     TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    job_type        TEXT DEFAULT '',
    salary          TEXT DEFAULT '',
    tags            TEXT DEFAULT '[]',
    scraped_at      TEXT DEFAULT (datetime('now')),
    quality_json    TEXT DEFAULT '{}',
    match_score     REAL DEFAULT 0,
    match_details   TEXT DEFAULT '{}',
    is_active       INTEGER DEFAULT 1,
    first_seen_at   TEXT DEFAULT (datetime('now')),
    last_seen_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(normalized_url)
);

-- Application tracking pipeline
CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES jobs(id),
    status      TEXT DEFAULT 'saved',
    applied_at  TEXT DEFAULT '',
    updated_at  TEXT DEFAULT (datetime('now')),
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(job_id)
);

-- Notes on applications
CREATE TABLE IF NOT EXISTS notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id),
    content         TEXT NOT NULL,
    note_type       TEXT DEFAULT 'general',
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Dismissed jobs (user said "not interested")
CREATE TABLE IF NOT EXISTS dismissed_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES jobs(id),
    reason      TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(job_id)
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_jobs_normalized_url ON jobs(normalized_url);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_is_active ON jobs(is_active);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_job_id ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_notes_application ON notes(application_id);
"""


def init_db():
    """Create all tables if they don't exist. Safe to call multiple times."""
    conn = get_connection()
    conn.executescript(_SCHEMA_SQL)
    # Set schema version
    conn.execute(
        "INSERT OR IGNORE INTO schema_info (key, value) VALUES ('version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()

    # Migration: add profile_slug columns to tables that don't have it in schema
    for table in ("user_skills", "jobs", "applications", "dismissed_jobs"):
        _ensure_column(table, "profile_slug", "TEXT", "''")

    # Create indexes for profile-scoped queries
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_profile ON jobs(profile_slug)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_profile ON applications(profile_slug)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dismissed_profile ON dismissed_jobs(profile_slug)")
        conn.commit()
    except Exception:
        pass

    print(f"  [OK] Database initialized ({_db_path()})")


def _ensure_column(table: str, column: str, col_type: str, default: str = ""):
    """Add a column if it doesn't exist (non-destructive migration)."""
    conn = get_connection()
    try:
        conn.execute(f"SELECT {column} FROM {table} LIMIT 1")
    except sqlite3.OperationalError:
        default_clause = f"DEFAULT {default}" if default else ""
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type} {default_clause}")
        conn.commit()


# ─── CRUD Helpers ────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return {}
    return dict(row)


def _parse_json_field(value: str, fallback=None):
    """Parse a JSON text field, returning fallback on failure."""
    if not value:
        return fallback if fallback is not None else []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback if fallback is not None else []


# ── Profile ──────────────────────────────────────────────────────────

def get_profile() -> Dict[str, Any]:
    """Get the user profile for the active profile slug."""
    conn = get_connection()
    slug = get_active_profile()
    row = conn.execute(
        "SELECT * FROM user_profile WHERE profile_slug = ?", (slug,)
    ).fetchone()
    if not row:
        # Fallback: legacy single-row
        row = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
    if not row:
        return {}
    d = _row_to_dict(row)
    # Parse JSON list fields
    for field in ("desired_roles", "desired_job_types", "desired_locations", "desired_companies"):
        d[field] = _parse_json_field(d.get(field, "[]"), [])
    d["ats_report"] = _parse_json_field(d.get("ats_report_json", "{}"), {})
    return d


def upsert_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update the user profile for the active profile slug."""
    conn = get_connection()
    slug = get_active_profile()
    data["profile_slug"] = slug

    # Serialize list fields to JSON
    for field in ("desired_roles", "desired_job_types", "desired_locations", "desired_companies"):
        if field in data and isinstance(data[field], (list, dict)):
            data[field] = json.dumps(data[field])
    if "ats_report" in data:
        data["ats_report_json"] = json.dumps(data.pop("ats_report"))

    data["updated_at"] = datetime.now().isoformat()

    existing = conn.execute(
        "SELECT id FROM user_profile WHERE profile_slug = ?", (slug,)
    ).fetchone()
    if existing:
        row_id = existing["id"]
        set_clause = ", ".join(f"{k} = ?" for k in data.keys())
        conn.execute(
            f"UPDATE user_profile SET {set_clause} WHERE id = ?",
            list(data.values()) + [row_id],
        )
    else:
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        conn.execute(
            f"INSERT INTO user_profile ({cols}) VALUES ({placeholders})",
            list(data.values()),
        )
    conn.commit()
    return get_profile()


# ── Skills ───────────────────────────────────────────────────────────

def get_skills() -> List[Dict[str, Any]]:
    conn = get_connection()
    slug = get_active_profile()
    rows = conn.execute(
        "SELECT * FROM user_skills WHERE profile_slug = ? ORDER BY name", (slug,)
    ).fetchall()
    if not rows:
        # Fallback: legacy rows with empty slug
        rows = conn.execute(
            "SELECT * FROM user_skills WHERE profile_slug = '' ORDER BY name"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def set_skills(skills: List[Dict[str, Any]]):
    """Replace all skills for the active profile."""
    conn = get_connection()
    slug = get_active_profile()
    conn.execute("DELETE FROM user_skills WHERE profile_slug = ?", (slug,))
    for s in skills:
        conn.execute(
            "INSERT OR REPLACE INTO user_skills (name, category, level, years, from_cv, confirmed, profile_slug) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                s.get("name", "").lower().strip(),
                s.get("category", ""),
                s.get("level", "intermediate"),
                s.get("years", 0),
                1 if s.get("from_cv", True) else 0,
                1 if s.get("confirmed", True) else 0,
                slug,
            ),
        )
    conn.commit()


def add_skill(name: str, category: str = "", level: str = "intermediate", years: float = 0, from_cv: bool = False):
    conn = get_connection()
    slug = get_active_profile()
    conn.execute(
        "INSERT OR IGNORE INTO user_skills (name, category, level, years, from_cv, confirmed, profile_slug) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (name.lower().strip(), category, level, years, 1 if from_cv else 0, slug),
    )
    conn.commit()


def remove_skill(name: str):
    conn = get_connection()
    slug = get_active_profile()
    conn.execute("DELETE FROM user_skills WHERE name = ? AND profile_slug = ?", (name.lower().strip(), slug))
    conn.commit()


# ── Jobs ─────────────────────────────────────────────────────────────

def upsert_job(job_data: Dict[str, Any]) -> int:
    """Insert or update a job. Returns the job id."""
    conn = get_connection()
    norm_url = job_data.get("normalized_url", job_data.get("url", ""))

    existing = conn.execute(
        "SELECT id FROM jobs WHERE normalized_url = ?", (norm_url,)
    ).fetchone()

    now = datetime.now().isoformat()
    tags_json = json.dumps(job_data.get("tags", []))
    quality_json = json.dumps(job_data.get("quality", {}))
    match_details_json = json.dumps(job_data.get("match_details", {}))

    if existing:
        job_id = existing["id"]
        conn.execute(
            """UPDATE jobs SET
                title=?, company=?, location=?, description=?,
                posted_date=?, source=?, job_type=?, salary=?,
                tags=?, quality_json=?, match_score=?, match_details=?,
                is_active=1, last_seen_at=?
            WHERE id=?""",
            (
                job_data.get("title", ""),
                job_data.get("company", ""),
                job_data.get("location", ""),
                job_data.get("description", ""),
                str(job_data.get("posted_date", "")),
                job_data.get("source", ""),
                job_data.get("job_type", ""),
                job_data.get("salary", ""),
                tags_json,
                quality_json,
                job_data.get("match_score", 0),
                match_details_json,
                now,
                job_id,
            ),
        )
    else:
        slug = get_active_profile()
        cursor = conn.execute(
            """INSERT INTO jobs
                (title, company, location, url, normalized_url, description,
                 posted_date, source, job_type, salary, tags, scraped_at,
                 quality_json, match_score, match_details, first_seen_at, last_seen_at, profile_slug)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_data.get("title", ""),
                job_data.get("company", ""),
                job_data.get("location", ""),
                job_data.get("url", ""),
                norm_url,
                job_data.get("description", ""),
                str(job_data.get("posted_date", "")),
                job_data.get("source", ""),
                job_data.get("job_type", ""),
                job_data.get("salary", ""),
                tags_json,
                now,
                quality_json,
                job_data.get("match_score", 0),
                match_details_json,
                now,
                now,
                slug,
            ),
        )
        job_id = cursor.lastrowid

    conn.commit()
    return job_id


def bulk_upsert_jobs(jobs_data: List[Dict[str, Any]]) -> int:
    """Insert/update multiple jobs in a single transaction. Returns count inserted."""
    count = 0
    with transaction() as conn:
        for jd in jobs_data:
            norm_url = jd.get("normalized_url", jd.get("url", ""))
            if not norm_url:
                continue
            jd["normalized_url"] = norm_url
            upsert_job(jd)
            count += 1
    return count


def get_jobs(
    active_only: bool = True,
    min_match: float = 0,
    source: str = "",
    location: str = "",
    limit: int = 500,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Query jobs with filters, scoped to active profile."""
    conn = get_connection()
    slug = get_active_profile()
    clauses = ["profile_slug = ?"]
    params = [slug]

    if active_only:
        clauses.append("is_active = 1")
    if min_match > 0:
        clauses.append("match_score >= ?")
        params.append(min_match)
    if source:
        clauses.append("LOWER(source) = ?")
        params.append(source.lower())
    if location:
        clauses.append("LOWER(location) LIKE ?")
        params.append(f"%{location.lower()}%")

    where = "WHERE " + " AND ".join(clauses)
    sql = f"SELECT * FROM jobs {where} ORDER BY match_score DESC, last_seen_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = _row_to_dict(r)
        d["tags"] = _parse_json_field(d.get("tags", "[]"), [])
        d["quality"] = _parse_json_field(d.get("quality_json", "{}"), {})
        d["match_details"] = _parse_json_field(d.get("match_details", "{}"), {})
        result.append(d)
    return result


def get_job_by_id(job_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    d["tags"] = _parse_json_field(d.get("tags", "[]"), [])
    d["quality"] = _parse_json_field(d.get("quality_json", "{}"), {})
    d["match_details"] = _parse_json_field(d.get("match_details", "{}"), {})
    return d


def get_job_count(active_only: bool = True) -> int:
    conn = get_connection()
    slug = get_active_profile()
    clauses = ["profile_slug = ?"]
    params = [slug]
    if active_only:
        clauses.append("is_active = 1")
    where = "WHERE " + " AND ".join(clauses)
    row = conn.execute(f"SELECT COUNT(*) as cnt FROM jobs {where}", params).fetchone()
    return row["cnt"] if row else 0


def mark_jobs_inactive(job_ids: List[int]):
    """Mark jobs as inactive (dead links, expired)."""
    conn = get_connection()
    placeholders = ",".join("?" for _ in job_ids)
    conn.execute(f"UPDATE jobs SET is_active = 0 WHERE id IN ({placeholders})", job_ids)
    conn.commit()


# ── Applications ─────────────────────────────────────────────────────

VALID_STATUSES = {
    "saved", "preparing", "applied", "phone_screen",
    "interview", "offer", "accepted", "rejected", "ghosted", "dismissed",
}


def save_application(job_id: int, status: str = "saved") -> Dict[str, Any]:
    conn = get_connection()
    slug = get_active_profile()
    now = datetime.now().isoformat()
    applied_at = now if status == "applied" else ""
    conn.execute(
        "INSERT OR REPLACE INTO applications (job_id, status, applied_at, updated_at, created_at, profile_slug) "
        "VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM applications WHERE job_id = ?), ?), ?)",
        (job_id, status, applied_at, now, job_id, now, slug),
    )
    conn.commit()
    return get_application(job_id)


def update_application_status(job_id: int, status: str) -> Dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    conn = get_connection()
    now = datetime.now().isoformat()
    applied_at_update = ""
    if status == "applied":
        applied_at_update = ", applied_at = ?"
    sql = f"UPDATE applications SET status = ?, updated_at = ?{applied_at_update} WHERE job_id = ?"
    params = [status, now]
    if status == "applied":
        params.append(now)
    params.append(job_id)
    conn.execute(sql, params)
    conn.commit()
    return get_application(job_id)


def get_application(job_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute(
        """SELECT a.*, j.title, j.company, j.location, j.url, j.match_score, j.source
           FROM applications a JOIN jobs j ON a.job_id = j.id
           WHERE a.job_id = ?""",
        (job_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_applications(status: str = "") -> List[Dict[str, Any]]:
    conn = get_connection()
    slug = get_active_profile()
    if status:
        rows = conn.execute(
            """SELECT a.*, j.title, j.company, j.location, j.url, j.match_score, j.source
               FROM applications a JOIN jobs j ON a.job_id = j.id
               WHERE a.profile_slug = ? AND a.status = ? ORDER BY a.updated_at DESC""",
            (slug, status),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT a.*, j.title, j.company, j.location, j.url, j.match_score, j.source
               FROM applications a JOIN jobs j ON a.job_id = j.id
               WHERE a.profile_slug = ? ORDER BY a.updated_at DESC""",
            (slug,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_pipeline_summary() -> Dict[str, int]:
    conn = get_connection()
    slug = get_active_profile()
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM applications WHERE profile_slug = ? GROUP BY status",
        (slug,),
    ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}


# ── Notes ────────────────────────────────────────────────────────────

def add_note(application_id: int, content: str, note_type: str = "general") -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO notes (application_id, content, note_type) VALUES (?, ?, ?)",
        (application_id, content, note_type),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_dict(row)


def get_notes(application_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM notes WHERE application_id = ? ORDER BY created_at DESC",
        (application_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── Dismissed ────────────────────────────────────────────────────────

def dismiss_job(job_id: int, reason: str = ""):
    conn = get_connection()
    slug = get_active_profile()
    conn.execute(
        "INSERT OR IGNORE INTO dismissed_jobs (job_id, reason, profile_slug) VALUES (?, ?, ?)",
        (job_id, reason, slug),
    )
    conn.commit()


def is_dismissed(job_id: int) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM dismissed_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return row is not None


def get_dismissed_job_ids() -> set:
    conn = get_connection()
    slug = get_active_profile()
    rows = conn.execute(
        "SELECT job_id FROM dismissed_jobs WHERE profile_slug = ?", (slug,)
    ).fetchall()
    return {r["job_id"] for r in rows}


def clear_all_job_data() -> Dict[str, int]:
    """
    Wipe all job-related data for the active profile.
    Returns counts of deleted rows per table.
    """
    conn = get_connection()
    slug = get_active_profile()
    counts = {}
    for table in ("dismissed_jobs", "applications", "jobs"):
        try:
            row = conn.execute(
                f"SELECT COUNT(*) as c FROM {table} WHERE profile_slug = ?", (slug,)
            ).fetchone()
            counts[table] = row["c"] if row else 0
            conn.execute(f"DELETE FROM {table} WHERE profile_slug = ?", (slug,))
        except Exception:
            counts[table] = 0
    # Notes are linked via application_id, orphaned notes auto-cleaned
    try:
        conn.execute("DELETE FROM notes WHERE application_id NOT IN (SELECT id FROM applications)")
    except Exception:
        pass
    conn.commit()
    return counts

