"""
description_enricher.py — Fetch full job descriptions from URLs.

Most scrapers (DDG, Google, niche boards) only return 50-200 char snippets.
This module visits each job URL and extracts the full description text,
giving the matching algorithm 10x more signal to work with.

Two strategies:
  1. Plain HTTP + BeautifulSoup (fast, works for most static pages)
  2. Stealth browser rendering (for JS-rendered SPAs, used as fallback)
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from scrapers.base import JobPosting

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")

# CSS selectors likely to contain job descriptions (ordered by specificity)
DESCRIPTION_SELECTORS = [
    "[class*='job-description']",
    "[class*='jobDescription']",
    "[class*='job_description']",
    "[class*='posting-description']",
    "[class*='job-detail']",
    "[class*='jobDetail']",
    "[class*='vacancy-description']",
    "[id*='job-description']",
    "[id*='jobDescription']",
    "[data-testid*='description']",
    "[data-testid*='job']",
    ".job-posting-section",
    ".job-content",
    ".posting-content",
    ".content-section",
    "article.job",
    "article",
    "[role='main']",
    "main",
    ".content",
]

# User agent rotation for HTTP requests
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# Minimum chars to consider a description "rich enough"
MIN_RICH_DESCRIPTION = 200


def enrich_job_descriptions(
    jobs: List[JobPosting],
    max_workers: int = 10,
    min_description_length: int = MIN_RICH_DESCRIPTION,
    logger=None,
) -> List[JobPosting]:
    """
    Enrich jobs that have thin descriptions by fetching the full page content.
    Modifies jobs in-place and returns the list.
    """
    thin_jobs = [j for j in jobs if len(j.description or "") < min_description_length]

    if not thin_jobs:
        if logger:
            logger(f"[Enricher] All {len(jobs)} jobs already have rich descriptions.")
        return jobs

    if logger:
        logger(f"[Enricher] Enriching {len(thin_jobs)}/{len(jobs)} jobs with thin descriptions...")

    enriched_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_job = {
            executor.submit(_fetch_description_http, job.url): job
            for job in thin_jobs
        }

        for future in as_completed(future_to_job):
            job = future_to_job[future]
            try:
                full_desc = future.result()
                if full_desc and len(full_desc) > len(job.description or ""):
                    job.description = full_desc[:5000]
                    enriched_count += 1
            except Exception:
                pass

    if logger:
        logger(f"[Enricher] Enriched {enriched_count}/{len(thin_jobs)} job descriptions.")

    return jobs


def _fetch_description_http(url: str) -> Optional[str]:
    """Fetch job description via plain HTTP + BeautifulSoup."""
    import random

    try:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        }
        resp = requests.get(url, timeout=12, headers=headers, allow_redirects=True)

        if resp.status_code != 200:
            return None

        # Skip non-HTML responses
        content_type = resp.headers.get("Content-Type", "")
        if "html" not in content_type and "text" not in content_type:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove script/style noise
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()

        # Try each selector in order of specificity
        for selector in DESCRIPTION_SELECTORS:
            try:
                el = soup.select_one(selector)
                if el:
                    text = el.get_text(separator="\n", strip=True)
                    if len(text) >= MIN_RICH_DESCRIPTION:
                        return _clean_text(text)
            except Exception:
                continue

        # Last resort: get body text
        body = soup.find("body")
        if body:
            text = body.get_text(separator="\n", strip=True)
            if len(text) >= MIN_RICH_DESCRIPTION:
                return _clean_text(text[:5000])

        return None

    except Exception:
        return None


def _clean_text(text: str) -> str:
    """Clean extracted text."""
    text = TAG_RE.sub(" ", text)
    text = SPACE_RE.sub(" ", text)
    # Remove very long lines (often CSS/JS artifacts)
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        line = line.strip()
        if len(line) > 500:
            line = line[:500]
        if line:
            cleaned.append(line)
    return "\n".join(cleaned).strip()
