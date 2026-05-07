"""
quality_gate.py — Deterministic filtering and scoring for job postings.
"""

import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from scrapers.base import JobPosting, is_listing_page, is_profile_or_people_page


TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref",
    "ref_src",
    "trk",
    "trkinfo",
    "utm_campaign",
    "utm_content",
    "utm_id",
    "utm_medium",
    "utm_source",
    "utm_term",
}
TRACKING_PREFIXES = ("utm_",)

GENERIC_COMPANIES = {
    "company",
    "employer",
    "hiring team",
    "job board",
    "recruiting team",
    "unknown",
    "web",
}
NOISE_URL_HINTS = (
    "curriculum-vitae",
    "karrierebibel",
    "lebenslauf",
)
NOISE_TEXT_HINTS = (
    "resume",
    "salary",
    "salaries",
    "profile",
    "cv template",
    "lebenslauf",
)
# Domains that can NEVER contain real job postings (DDG garbage)
GARBAGE_DOMAINS = {
    # Porn / adult
    "porn", "xxx", "xnxx", "xhamster", "pornhub", "xvideos", "redtube",
    "youporn", "tube8", "fapvid", "tubepleasure", "colliderporn",
    # App stores / downloads
    "apkpure", "play.google", "apps.apple", "microsoft.com/store",
    "win-rar", "download.cnet",
    # Forums / Q&A (not job sites)
    "vk.com", "reddit.com", "quora.com", "stackoverflow.com",
    "zhihu.com", "baidu.com", "win11forum", "windowsarea",
    # Translation / reference
    "translate.google", "deepl.com", "dict.cc", "linguee",
    "lemedecin", "medicaments",
    # Gaming / shopping
    "smartgaming-shop", "steam", "epicgames",
    # Museums / tourism
    "museumexplorer", "thetrainpark", "wanderer",
    # Social / video
    "tubitv", "tiktok.com", "instagram.com", "facebook.com",
    "twitter.com", "x.com", "youtube.com",
    # Auth / SSO / accounts pages
    "accounts.google", "login.microsoft", "sso.",
    # Misc non-job
    "fintiba", "bluetooths", "copilot.microsoft",
    "hoebeginik", "ideenraum", "digitalstudioweb",
    "presse.", "blog.", "hilfe.",
}
DIRECT_JOB_HINTS = (
    "career",
    "careers",
    "job",
    "jobs",
    "jobid",
    "karriere",
    "stellen",
    "vacancy",
    "workdayjobs",
)
TRUSTED_SOURCE_HINTS = (
    "arbeitnow",
    "adzuna",
    "arbeitsagentur",
    "companyportals",
    "ddg",
    "google",
    "glassdoor",
    "greenhouse",
    "indeed",
    "jobteaser",
    "lever",
    "linkedin",
    "nicheboards",
    "remoteok",
    "smartrecruiters",
    "sitemapminer",
    "stepstone",
    "xing",
)
ROLE_HINTS = (
    "ai",
    "analytics",
    "backend",
    "cloud",
    "data",
    "developer",
    "devops",
    "engineer",
    "machine learning",
    "ml",
    "python",
    "research",
    "software",
    "thesis",
    # Systems Engineering / MBSE / Automotive
    "architect",
    "ingenieur",
    "konstruktion",
    "mbse",
    "requirements",
    "safety",
    "systems",
    "validation",
    "verification",
)

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
NOISE_TITLE_RE = re.compile(r"(?i)(salary|resume|profile|lebenslauf|curriculum vitae)")


def _clean_text(value: Any) -> str:
    text = unescape(str(value or ""))
    text = TAG_RE.sub(" ", text)
    return SPACE_RE.sub(" ", text).strip()


def normalize_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""

    try:
        parts = urlsplit(text)
        if not parts.scheme:
            parts = urlsplit(f"https://{text}")

        filtered_query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            key_l = key.lower()
            if key_l in TRACKING_KEYS or key_l.startswith(TRACKING_PREFIXES):
                continue
            filtered_query.append((key, value))
        filtered_query.sort()

        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(filtered_query), ""))
    except Exception:
        return text


def parse_posted_date(value: Any) -> Optional[datetime]:
    if not value:
        return None

    if isinstance(value, datetime):
        return value.replace(tzinfo=None)

    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 10_000_000_000:
                ts /= 1000
            return datetime.fromtimestamp(ts)

        text = str(value).strip()
        if not text:
            return None
        if text.isdigit():
            ts = float(text)
            if ts > 10_000_000_000:
                ts /= 1000
            return datetime.fromtimestamp(ts)

        lowered = text.lower()
        now = datetime.now()
        if any(token in lowered for token in ("just now", "moment", "gerade", "jetzt", "today", "heute")):
            return now
        if "yesterday" in lowered or "gestern" in lowered:
            return now - timedelta(days=1)
        if "hour" in lowered or "stunde" in lowered:
            return now - timedelta(hours=1)
        if "minute" in lowered or "min" in lowered:
            return now - timedelta(minutes=15)

        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass

        try:
            return parsedate_to_datetime(text).replace(tzinfo=None)
        except Exception:
            pass

        formats = (
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%Y %m %d",
            "%d.%m.%Y",
            "%Y %m %d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        )
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    except Exception:
        return None

    return None


def _normalize_company(company: str) -> str:
    text = _clean_text(company)
    via_match = re.fullmatch(r"\(via\s+(.+?)\)", text, re.IGNORECASE)
    if via_match:
        return via_match.group(1).strip()
    return text


def _threshold_for_stage(config: Optional[dict], stage: str) -> int:
    config = config or {}
    mode = str(config.get("mode", "balanced")).lower()
    if stage == "batch":
        return int(config.get("batch_min_score", 20))
    if mode == "strict":
        return int(config.get("final_min_score", 55))
    if mode == "lenient":
        return int(config.get("final_min_score", 25))
    return int(config.get("final_min_score", 40))


def _quality_bucket(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def assess_job_quality(job: JobPosting, config: Optional[dict] = None, stage: str = "final") -> Dict[str, Any]:
    config = config or {}
    title = _clean_text(job.title)
    company = _normalize_company(job.company)
    location = _clean_text(job.location)
    description = _clean_text(job.description)
    source = _clean_text(job.source).lower()
    normalized_url = normalize_url(job.url)
    parsed_date = parse_posted_date(job.posted_date)

    min_title_chars = int(config.get("min_title_chars", 8))
    max_title_chars = int(config.get("max_title_chars", 160))
    min_description_chars = int(config.get("min_description_chars", 50))
    score = 0
    flags = []
    reject_reason = ""
    trusted_source = any(hint in source for hint in TRUSTED_SOURCE_HINTS)
    haystack = " ".join([title.lower(), description.lower(), normalized_url.lower(), source])

    if not normalized_url or not normalized_url.startswith(("http://", "https://")):
        reject_reason = "invalid_url"
    elif any(gd in normalized_url.lower() for gd in GARBAGE_DOMAINS):
        reject_reason = "garbage_domain"
    elif is_profile_or_people_page(normalized_url):
        reject_reason = "noise_url"
    elif is_listing_page(title, normalized_url):
        reject_reason = "listing_page"
    elif any(hint in normalized_url.lower() for hint in NOISE_URL_HINTS):
        reject_reason = "noise_url"
    elif NOISE_TITLE_RE.search(title):
        reject_reason = "noise_title"
    elif len(title) < min_title_chars or len(title) > max_title_chars:
        reject_reason = "title_length"
    elif not location:
        reject_reason = "missing_location"
    elif not company and not trusted_source:
        reject_reason = "missing_company"

    if reject_reason:
        return {
            "accepted": False,
            "score": 0,
            "bucket": "low",
            "flags": flags,
            "reject_reason": reject_reason,
            "normalized_url": normalized_url,
            "trusted_source": trusted_source,
            "stage": stage,
        }

    score += 35

    if 20 <= len(title) <= 100:
        score += 15
    else:
        flags.append("title_shape")
        score += 6

    if any(hint in haystack for hint in ROLE_HINTS):
        score += 10
    else:
        flags.append("weak_role_signal")

    if company and company.lower() not in GENERIC_COMPANIES:
        score += 10
    else:
        flags.append("generic_company")

    if trusted_source:
        score += 10
    else:
        flags.append("untrusted_source")

    if any(hint in normalized_url.lower() for hint in DIRECT_JOB_HINTS):
        score += 10
    else:
        flags.append("weak_url_path")

    if location:
        score += 5

    if len(description) >= min_description_chars:
        score += 15
    elif description:
        score += 5
        flags.append("short_description")
    else:
        flags.append("missing_description")

    if parsed_date:
        now = datetime.now()
        age = now - parsed_date
        if age < timedelta(days=0):
            flags.append("future_date")
        elif age <= timedelta(days=30):
            score += 5
        elif age <= timedelta(days=90):
            score += 3
        else:
            flags.append("stale_posting")
    else:
        flags.append("unparsed_date")

    threshold = _threshold_for_stage(config, stage)
    accepted = score >= threshold
    if not accepted:
        reject_reason = f"score_below_{threshold}"

    return {
        "accepted": accepted,
        "score": min(score, 100),
        "bucket": _quality_bucket(score),
        "flags": flags,
        "reject_reason": reject_reason,
        "normalized_url": normalized_url,
        "trusted_source": trusted_source,
        "parsed_posted_date": parsed_date.isoformat() if parsed_date else None,
        "stage": stage,
    }


def apply_quality_gate(job: JobPosting, config: Optional[dict] = None, stage: str = "final") -> Dict[str, Any]:
    result = assess_job_quality(job, config=config, stage=stage)
    job.title = _clean_text(job.title)
    job.company = _normalize_company(job.company)
    job.location = _clean_text(job.location)
    job.description = _clean_text(job.description)
    job.url = result.get("normalized_url") or normalize_url(job.url)
    existing = dict(job.quality or {})
    existing.update(result)
    job.quality = existing
    return result


def summarize_quality_jobs(jobs: Iterable[JobPosting]) -> Dict[str, Any]:
    jobs = list(jobs)
    if not jobs:
        return {
            "count": 0,
            "average_score": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "actionable_links": 0,
        }

    scores = [int((job.quality or {}).get("score", 0)) for job in jobs]
    high = sum(1 for score in scores if score >= 80)
    medium = sum(1 for score in scores if 55 <= score < 80)
    low = len(scores) - high - medium
    actionable_links = sum(
        1
        for job in jobs
        if (job.quality or {}).get("url_status") in {"alive", "redirected"}
    )
    return {
        "count": len(jobs),
        "average_score": round(sum(scores) / len(scores), 1),
        "high": high,
        "medium": medium,
        "low": low,
        "actionable_links": actionable_links,
    }
