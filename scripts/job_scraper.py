"""
job_scraper.py — Multi-source job scraper orchestrator.
Coordinates all scraper backends, handles deduplication, caching, and rate limiting.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List

import requests
import yaml
from rapidfuzz import fuzz

from scrapers.base import JobPosting, is_listing_page
from scrapers.arbeitnow import ArbeitnowScraper
from scrapers.google_jobs import GoogleJobsScraper
from scrapers.linkedin import LinkedInScraper
from scrapers.indeed import IndeedScraper
from scrapers.stepstone import StepStoneScraper
from scrapers.xing import XingScraper
from scrapers.jobteaser import JobteaserScraper
from scrapers.adzuna import AdzunaScraper
from scrapers.remoteok import RemoteOKScraper


# Registry of all available scrapers
SCRAPER_REGISTRY = {
    "arbeitnow": ArbeitnowScraper,
    "google_jobs": GoogleJobsScraper,
    "linkedin": LinkedInScraper,
    "indeed": IndeedScraper,
    "stepstone": StepStoneScraper,
    "xing": XingScraper,
    "jobteaser": JobteaserScraper,
    "adzuna": AdzunaScraper,
    "remoteok": RemoteOKScraper,
}


def load_config(config_path: str = None) -> dict:
    """Load configuration from config.yaml."""
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.yaml",
        )
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def load_cache(cache_path: str) -> Dict:
    """Load cached job listings."""
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"jobs": [], "timestamp": None}


def save_cache(cache_path: str, jobs: List[JobPosting]):
    """Save job listings to cache."""
    cache_data = {
        "jobs": [j.to_dict() for j in jobs],
        "timestamp": datetime.now().isoformat(),
    }
    with open(cache_path, "w") as f:
        json.dump(cache_data, f, indent=2, default=str)


def is_cache_valid(cache_path: str, expiry_hours: int = 24) -> bool:
    """Check if cache exists and is still within expiry window."""
    cache = load_cache(cache_path)
    if not cache.get("timestamp"):
        return False
    try:
        cached_time = datetime.fromisoformat(cache["timestamp"])
        return datetime.now() - cached_time < timedelta(hours=expiry_hours)
    except (ValueError, TypeError):
        return False


def deduplicate_jobs(jobs: List[JobPosting], threshold: int = 80) -> List[JobPosting]:
    """
    Remove duplicate job postings based on URL exact match
    and fuzzy title+company matching.
    Also removes listing/search pages that slipped through.
    """
    unique = []
    seen_urls = set()

    for job in jobs:
        # Exact URL match
        if job.url in seen_urls:
            continue

        # Filter out listing/search pages (title like "257 jobs in Berlin")
        if is_listing_page(job.title, job.url):
            continue

        seen_urls.add(job.url)

        # Fuzzy match against existing jobs
        is_dup = False
        for existing in unique:
            title_score = fuzz.token_set_ratio(
                job.title.lower(), existing.title.lower()
            )
            company_score = fuzz.token_set_ratio(
                job.company.lower(), existing.company.lower()
            )
            # If both title and company are very similar, it's a duplicate
            if title_score > threshold and company_score > threshold:
                is_dup = True
                break
            # If title is nearly identical and same location
            if (
                title_score > 90
                and job.location.lower() == existing.location.lower()
            ):
                is_dup = True
                break

        if not is_dup:
            unique.append(job)

    return unique


_VERIFY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


def _check_url(job: JobPosting) -> bool:
    """Return True if the job URL appears to be a live page (not 404/gone)."""
    try:
        resp = requests.head(
            job.url, headers=_VERIFY_HEADERS,
            timeout=8, allow_redirects=True,
        )
        # Accept any 2xx or 3xx; reject 404, 410, 403 (often means blocked/gone)
        if resp.status_code < 400:
            return True
        # Some sites block HEAD but accept GET — try a light GET
        if resp.status_code in (403, 405):
            resp = requests.get(
                job.url, headers=_VERIFY_HEADERS,
                timeout=8, allow_redirects=True, stream=True,
            )
            resp.close()
            return resp.status_code < 400
        return False
    except requests.RequestException:
        # Timeout / connection error — give the benefit of the doubt
        return True


def verify_job_urls(
    jobs: List[JobPosting], logger=print, max_workers: int = 10
) -> List[JobPosting]:
    """Verify job URLs in parallel, removing dead links."""
    if not jobs:
        return jobs

    verified = []
    dead = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_job = {pool.submit(_check_url, j): j for j in jobs}
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            try:
                if future.result():
                    verified.append(job)
                else:
                    dead += 1
            except Exception:
                verified.append(job)  # on error, keep the job

    if dead:
        logger(f"  🗑 Removed {dead} dead/expired job links")
    return verified


def scrape_all_jobs(
    config: dict = None,
    config_path: str = None,
    use_cache: bool = True,
    scrapers_to_use: List[str] = None,
    cities: List[str] = None,
    keywords: List[str] = None,
    job_types: List[str] = None,
    logger=print,
    on_batch=None,
) -> List[JobPosting]:
    """
    Run all configured scrapers and return a deduplicated list of JobPostings.

    Args:
        config: Pre-loaded config dict (optional).
        config_path: Path to config.yaml (optional).
        use_cache: Whether to use cached results if available.
        scrapers_to_use: List of scraper names to use (None = all from config).

    Returns:
        List of deduplicated JobPosting objects.
    """
    if config is None:
        config = load_config(config_path)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_path = os.path.join(project_root, config.get("paths", {}).get("cache_file", ".job_cache.json"))

    # Check cache
    cache_expiry = config.get("scraping", {}).get("cache_expiry_hours", 24)
    if use_cache and is_cache_valid(cache_path, cache_expiry):
        logger("✓ Using cached job listings (still valid).")
        cache = load_cache(cache_path)
        return [JobPosting.from_dict(j) for j in cache.get("jobs", [])]

    # Determine scrapers to run
    scraping_config = config.get("scraping", {})
    priority = scraping_config.get("scraper_priority", list(SCRAPER_REGISTRY.keys()))

    if scrapers_to_use:
        priority = [s for s in priority if s in scrapers_to_use]

    cities = cities or config.get("cities", ["Berlin", "Wolfsburg", "Leipzig"])
    keywords = keywords or config.get("search_keywords", ["Data Engineering", "Backend"])
    job_types = job_types or config.get("job_types", ["Werkstudent", "Internship"])

    all_jobs: List[JobPosting] = []

    for scraper_name in priority:
        scraper_cls = SCRAPER_REGISTRY.get(scraper_name)
        if not scraper_cls:
            print(f"⚠ Unknown scraper: {scraper_name}, skipping.")
            continue

        scraper = scraper_cls(config=scraping_config)
        logger(f"\n{'─'*50}")
        logger(f"▶ Running {scraper.name} scraper...")
        logger(f"{'─'*50}")

        scraper_jobs = []
        for city in cities:
            logger(f"  📍 Searching in {city}...")
            try:
                city_jobs = scraper.scrape(city, keywords, job_types)
                scraper_jobs.extend(city_jobs)
                logger(f"    → Found {len(city_jobs)} jobs in {city}")
                if on_batch and city_jobs:
                    on_batch(city_jobs)
            except Exception as e:
                logger(f"    ✗ Error scraping {city}: {e}")

            time.sleep(1)  # Brief pause between cities

        all_jobs.extend(scraper_jobs)
        logger(f"  Total from {scraper.name}: {len(scraper_jobs)} jobs")

        # Incremental save after each scraper completes
        intermediate = deduplicate_jobs(all_jobs)
        save_cache(cache_path, intermediate)
        logger(f"  💾 Saved {len(intermediate)} jobs to cache (incremental)")

    # Final deduplication
    logger(f"\n{'─'*50}")
    logger(f"Final deduplication of {len(all_jobs)} total job listings...")
    unique_jobs = deduplicate_jobs(all_jobs)
    logger(f"After deduplication: {len(unique_jobs)} unique jobs")

    # Verify URLs are still alive
    logger(f"Verifying {len(unique_jobs)} job URLs...")
    unique_jobs = verify_job_urls(unique_jobs, logger=logger)
    logger(f"After verification: {len(unique_jobs)} verified jobs")

    # Save to cache
    save_cache(cache_path, unique_jobs)
    logger(f"✓ Cached {len(unique_jobs)} jobs to {cache_path}")

    return unique_jobs


def get_cached_jobs(config: dict = None, config_path: str = None) -> List[JobPosting]:
    """Load jobs from cache without scraping."""
    if config is None:
        config = load_config(config_path)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_path = os.path.join(project_root, config.get("paths", {}).get("cache_file", ".job_cache.json"))
    cache = load_cache(cache_path)
    return [JobPosting.from_dict(j) for j in cache.get("jobs", [])]


if __name__ == "__main__":
    print("ATSchecker — Job Scraper")
    print("=" * 60)
    jobs = scrape_all_jobs(use_cache=False)
    print(f"\n{'='*60}")
    print(f"Total unique jobs found: {len(jobs)}")
    for i, job in enumerate(jobs[:20], 1):
        print(f"\n{i}. {job.title}")
        print(f"   Company: {job.company}")
        print(f"   Location: {job.location}")
        print(f"   Source: {job.source}")
        print(f"   URL: {job.url}")
