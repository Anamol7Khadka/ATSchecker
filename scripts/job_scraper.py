"""
job_scraper.py — Multi-source job scraper orchestrator.
Coordinates all scraper backends, handles deduplication, caching, and rate limiting.
"""

import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List

import requests
import yaml
from rapidfuzz import fuzz

from quality_gate import apply_quality_gate, normalize_url
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
from scrapers.company_portals import CompanyPortalsScraper
from scrapers.jooble import JoobleScraper
from scrapers.rss_feeds import RSSJobScraper
from scrapers.arbeitsagentur import ArbeitsagenturScraper
from scrapers.greenhouse_api import GreenhouseAPIScraper
from scrapers.lever_api import LeverAPIScraper
from scrapers.sitemap_miner import SitemapMiner
from scrapers.niche_boards import NicheBoardsScraper
from scrapers.career_page_crawler import CareerPageCrawler
from scrapers.smartrecruiters_api import SmartRecruitersAPIScraper
from scrapers.rate_limiter import reset_state as reset_rate_limiter

# Description enricher (fetches full job page content for thin DDG snippets)
try:
    from description_enricher import enrich_job_descriptions
    ENRICHER_AVAILABLE = True
except ImportError:
    ENRICHER_AVAILABLE = False


# Registry of all available scrapers — ALL run in parallel for maximum coverage
SCRAPER_REGISTRY = {
    # ── API-based (most reliable, no anti-bot issues) ──
    "arbeitsagentur": ArbeitsagenturScraper,  # Germany's official employment agency
    "arbeitnow": ArbeitnowScraper,            # Free API, Germany-focused
    "jooble": JoobleScraper,                  # Aggregator (needs free API key)
    "adzuna": AdzunaScraper,                  # Free API (needs free API key)
    "rss_feeds": RSSJobScraper,               # RSS feeds (always works)
    "remoteok": RemoteOKScraper,              # Remote jobs API

    # ── Search engine aggregation ──
    "google_jobs": GoogleJobsScraper,          # DDG/Google → 80+ job sites
    "company_portals": CompanyPortalsScraper,  # DDG → company career pages

    # ── Selenium-based (may be blocked by anti-bot) ──
    "linkedin": LinkedInScraper,
    "indeed": IndeedScraper,
    "stepstone": StepStoneScraper,
    "xing": XingScraper,
    "jobteaser": JobteaserScraper,

    # ── Direct ATS APIs (public, unauthenticated — zero bot risk) ──
    "greenhouse_api": GreenhouseAPIScraper,
    "lever_api": LeverAPIScraper,
    "smartrecruiters_api": SmartRecruitersAPIScraper,

    # ── Passive discovery (zero bot risk) ──
    "sitemap_miner": SitemapMiner,

    # ── Niche boards (profile-aware) ──
    "niche_boards": NicheBoardsScraper,

    # ── Stealth deep crawling (company career SPAs) ──
    "career_crawler": CareerPageCrawler,
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
    url_to_index = {}

    def _job_quality_score(job: JobPosting) -> int:
        return int((job.quality or {}).get("score", 0))

    def _job_url_key(job: JobPosting) -> str:
        return (job.quality or {}).get("normalized_url") or normalize_url(job.url)

    def _prefer_job(candidate: JobPosting, existing: JobPosting) -> JobPosting:
        candidate_key = (
            _job_quality_score(candidate),
            len(candidate.description or ""),
            bool((candidate.quality or {}).get("trusted_source")),
            candidate.scraped_at,
        )
        existing_key = (
            _job_quality_score(existing),
            len(existing.description or ""),
            bool((existing.quality or {}).get("trusted_source")),
            existing.scraped_at,
        )
        return candidate if candidate_key >= existing_key else existing

    for job in jobs:
        # Exact URL match
        url_key = _job_url_key(job)
        if url_key in url_to_index:
            idx = url_to_index[url_key]
            unique[idx] = _prefer_job(job, unique[idx])
            continue

        # Filter out listing/search pages (title like "257 jobs in Berlin")
        if is_listing_page(job.title, job.url):
            continue

        # Fuzzy match against existing jobs
        is_dup = False
        for idx, existing in enumerate(unique):
            title_score = fuzz.token_set_ratio(
                job.title.lower(), existing.title.lower()
            )
            company_score = fuzz.token_set_ratio(
                job.company.lower(), existing.company.lower()
            )
            # If both title and company are very similar, it's a duplicate
            if title_score > threshold and company_score > threshold:
                unique[idx] = _prefer_job(job, existing)
                is_dup = True
                break
            # If title is nearly identical and same location
            if (
                title_score > 90
                and job.location.lower() == existing.location.lower()
            ):
                unique[idx] = _prefer_job(job, existing)
                is_dup = True
                break

        if not is_dup:
            url_to_index[url_key] = len(unique)
            unique.append(job)

    return unique


def _quality_metrics_template() -> Dict:
    return {
        "seen": 0,
        "accepted": 0,
        "rejected": 0,
        "reasons": {},
    }


def _merge_quality_metrics(target: Dict, source: Dict):
    target["seen"] += source.get("seen", 0)
    target["accepted"] += source.get("accepted", 0)
    target["rejected"] += source.get("rejected", 0)
    for reason, count in source.get("reasons", {}).items():
        target["reasons"][reason] = target["reasons"].get(reason, 0) + count


def _apply_quality_stage(
    jobs: List[JobPosting],
    quality_config: dict,
    stage: str,
) -> tuple[List[JobPosting], Dict]:
    accepted = []
    metrics = _quality_metrics_template()

    for job in jobs:
        metrics["seen"] += 1
        result = apply_quality_gate(job, config=quality_config, stage=stage)
        if result.get("accepted"):
            metrics["accepted"] += 1
            accepted.append(job)
            continue

        metrics["rejected"] += 1
        reason = result.get("reject_reason") or "rejected"
        metrics["reasons"][reason] = metrics["reasons"].get(reason, 0) + 1

    return accepted, metrics


_DEFAULT_VERIFY_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


def _verify_headers(scraping_config: dict = None) -> dict:
    scraping_config = scraping_config or {}
    use_pool = scraping_config.get("rotate_user_agents", True)
    configured = scraping_config.get("user_agents", []) if use_pool else []
    agents = configured or _DEFAULT_VERIFY_USER_AGENTS
    return {
        "User-Agent": random.choice(agents),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }


def _check_url(job: JobPosting, scraping_config: dict = None) -> Dict[str, str]:
    """Return verification status for a job URL."""
    scraping_config = scraping_config or {}
    retries = int(scraping_config.get("max_retries_per_task", 2))
    base = float(scraping_config.get("retry_backoff_base_seconds", 1.0))
    cap = float(scraping_config.get("retry_backoff_max_seconds", 20.0))
    jitter = float(scraping_config.get("request_jitter_seconds", 1.0))

    for attempt in range(retries + 1):
        try:
            headers = _verify_headers(scraping_config)
            resp = requests.head(
                job.url, headers=headers,
                timeout=8, allow_redirects=True,
            )
            if resp.status_code < 400:
                final_url = normalize_url(resp.url or job.url)
                status = "redirected" if final_url != normalize_url(job.url) else "alive"
                return {"keep": True, "status": status, "url": final_url}
            if resp.status_code in (403, 405):
                resp = requests.get(
                    job.url, headers=headers,
                    timeout=8, allow_redirects=True, stream=True,
                )
                resp.close()
                if resp.status_code < 400:
                    final_url = normalize_url(resp.url or job.url)
                    status = "redirected" if final_url != normalize_url(job.url) else "alive"
                    return {"keep": True, "status": status, "url": final_url}
                if resp.status_code in (403, 429):
                    return {"keep": False, "status": "blocked", "url": normalize_url(job.url)}
                return {"keep": False, "status": "dead", "url": normalize_url(job.url)}
            if resp.status_code in (403, 429):
                return {"keep": False, "status": "blocked", "url": normalize_url(job.url)}
            return {"keep": False, "status": "dead", "url": normalize_url(job.url)}
        except requests.RequestException:
            if attempt >= retries:
                drop_uncertain = bool(scraping_config.get("drop_uncertain_urls", True))
                return {
                    "keep": not drop_uncertain,
                    "status": "uncertain",
                    "url": normalize_url(job.url),
                }
            wait = min(cap, base * (2 ** attempt)) + random.uniform(0, jitter)
            time.sleep(wait)

    return {"keep": False, "status": "uncertain", "url": normalize_url(job.url)}


def verify_job_urls(
    jobs: List[JobPosting], logger=print, max_workers: int = 10, scraping_config: dict = None
) -> List[JobPosting]:
    """Verify job URLs in parallel, removing dead links."""
    if not jobs:
        return jobs

    verified = []
    dead = 0
    status_counts = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_job = {
            pool.submit(_check_url, j, scraping_config): j for j in jobs
        }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            try:
                result = future.result()
                status = result.get("status", "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
                job.url = result.get("url", job.url)
                quality = dict(job.quality or {})
                quality["url_status"] = status
                quality["verified_url"] = result.get("url", job.url)
                job.quality = quality
                if result.get("keep"):
                    verified.append(job)
                else:
                    dead += 1
            except Exception:
                verified.append(job)  # on error, keep the job

    if dead:
        logger(f"  🗑 Removed {dead} dead/expired job links")
    if status_counts:
        logger(
            "  🔗 URL verification: "
            + ", ".join(f"{name}={count}" for name, count in sorted(status_counts.items()))
        )
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
    quality_config = dict(config.get("quality", {}))

    # Check cache
    cache_expiry = config.get("scraping", {}).get("cache_expiry_hours", 24)
    if use_cache and is_cache_valid(cache_path, cache_expiry):
        logger("✓ Using cached job listings (still valid).")
        cache = load_cache(cache_path)
        cached_jobs = [JobPosting.from_dict(j) for j in cache.get("jobs", [])]
        cached_jobs, metrics = _apply_quality_stage(cached_jobs, quality_config, stage="final")
        if metrics["rejected"]:
            logger(
                f"  ✂ Quality gate removed {metrics['rejected']} cached jobs: "
                + ", ".join(f"{reason}={count}" for reason, count in sorted(metrics["reasons"].items()))
            )
        return deduplicate_jobs(cached_jobs)

    # Determine scrapers to run
    scraping_config = dict(config.get("scraping", {}))
    scraping_config["_logger"] = logger
    priority = scraping_config.get("scraper_priority", list(SCRAPER_REGISTRY.keys()))
    reset_rate_limiter()

    if scrapers_to_use:
        priority = [s for s in priority if s in scrapers_to_use]

    cities = cities or config.get("cities", ["Berlin", "Wolfsburg", "Leipzig"])
    keywords = keywords or config.get("search_keywords", ["Data Engineering", "Backend"])
    job_types = job_types or config.get("job_types", ["Werkstudent", "Internship"])

    all_jobs: List[JobPosting] = []

    max_keywords = int(scraping_config.get("max_keywords_per_city", len(keywords)))
    max_job_types = int(scraping_config.get("max_job_types", len(job_types)))
    keywords = keywords[:max_keywords]
    job_types = job_types[:max_job_types]

    parallel_scrapers = bool(scraping_config.get("parallel_scrapers", True))
    max_scraper_workers = max(1, int(scraping_config.get("max_scraper_workers", 4)))
    parallel_cities = bool(scraping_config.get("parallel_cities", True))
    max_city_workers = max(1, int(scraping_config.get("max_city_workers_per_scraper", 3)))
    retries = int(scraping_config.get("max_retries_per_task", 2))
    backoff_base = float(scraping_config.get("retry_backoff_base_seconds", 1.0))
    backoff_cap = float(scraping_config.get("retry_backoff_max_seconds", 20.0))
    jitter = float(scraping_config.get("request_jitter_seconds", 1.0))
    overall_quality_metrics = _quality_metrics_template()

    def _retry_wait(attempt: int):
        wait = min(backoff_cap, backoff_base * (2 ** attempt)) + random.uniform(0, jitter)
        time.sleep(wait)

    def _scrape_city(scraper_name: str, city_name: str):
        scraper_cls = SCRAPER_REGISTRY.get(scraper_name)
        if not scraper_cls:
            return city_name, [], Exception(f"Unknown scraper {scraper_name}")

        for attempt in range(retries + 1):
            scraper = scraper_cls(config=scraping_config)
            try:
                raw_jobs = scraper.scrape(city_name, keywords, job_types)
                city_jobs, metrics = _apply_quality_stage(raw_jobs, quality_config, stage="batch")
                return city_name, city_jobs, None, metrics, len(raw_jobs)
            except Exception as exc:
                if attempt >= retries:
                    return city_name, [], exc, _quality_metrics_template(), 0
                logger(
                    f"    [!] {scraper_name} failed for {city_name} (attempt {attempt + 1}/{retries + 1}): {exc}"
                )
                _retry_wait(attempt)

        return city_name, [], None, _quality_metrics_template(), 0

    def _run_scraper(scraper_name: str):
        scraper_cls = SCRAPER_REGISTRY.get(scraper_name)
        if not scraper_cls:
            logger(f"[!] Unknown scraper: {scraper_name}, skipping.")
            return scraper_name, []

        logger(f"\n{'─'*50}")
        logger(f"[>] Running {scraper_name} scraper...")
        logger(f"{'─'*50}")

        scraper_jobs: List[JobPosting] = []

        if parallel_cities and len(cities) > 1:
            city_workers = min(max_city_workers, len(cities))
            with ThreadPoolExecutor(max_workers=city_workers) as city_pool:
                futures = {
                    city_pool.submit(_scrape_city, scraper_name, city_name): city_name
                    for city_name in cities
                }
                for future in as_completed(futures):
                    city_name = futures[future]
                    try:
                        _, city_jobs, err, metrics, raw_count = future.result()
                        _merge_quality_metrics(overall_quality_metrics, metrics)
                        if err:
                            logger(f"    [x] Error scraping {city_name}: {err}")
                            continue
                        scraper_jobs.extend(city_jobs)
                        if metrics["rejected"]:
                            logger(
                                f"    → Found {len(city_jobs)} accepted / {raw_count} raw in {city_name}"
                            )
                        else:
                            logger(f"    → Found {len(city_jobs)} jobs in {city_name}")
                        if on_batch and city_jobs:
                            on_batch(city_jobs)
                    except Exception as exc:
                        logger(f"    [x] Error scraping {city_name}: {exc}")
        else:
            for city_name in cities:
                logger(f"  [>] Searching in {city_name}...")
                _, city_jobs, err, metrics, raw_count = _scrape_city(scraper_name, city_name)
                _merge_quality_metrics(overall_quality_metrics, metrics)
                if err:
                    logger(f"    [x] Error scraping {city_name}: {err}")
                    continue
                scraper_jobs.extend(city_jobs)
                if metrics["rejected"]:
                    logger(f"    → Found {len(city_jobs)} accepted / {raw_count} raw in {city_name}")
                else:
                    logger(f"    → Found {len(city_jobs)} jobs in {city_name}")
                if on_batch and city_jobs:
                    on_batch(city_jobs)
                time.sleep(random.uniform(0, jitter))

        logger(f"  Total from {scraper_name}: {len(scraper_jobs)} jobs")
        return scraper_name, scraper_jobs

    if parallel_scrapers and len(priority) > 1:
        scraper_workers = min(max_scraper_workers, len(priority))
        with ThreadPoolExecutor(max_workers=scraper_workers) as scraper_pool:
            future_to_scraper = {
                scraper_pool.submit(_run_scraper, scraper_name): scraper_name
                for scraper_name in priority
            }
            for future in as_completed(future_to_scraper):
                scraper_name = future_to_scraper[future]
                try:
                    _, scraper_jobs = future.result()
                    all_jobs.extend(scraper_jobs)
                    intermediate = deduplicate_jobs(all_jobs)
                    save_cache(cache_path, intermediate)
                    logger(
                        f"  [*] Saved {len(intermediate)} jobs to cache (incremental after {scraper_name})"
                    )
                except Exception as exc:
                    logger(f"[!] Scraper {scraper_name} failed: {exc}")
    else:
        for scraper_name in priority:
            _, scraper_jobs = _run_scraper(scraper_name)
            all_jobs.extend(scraper_jobs)
            intermediate = deduplicate_jobs(all_jobs)
            save_cache(cache_path, intermediate)
            logger(f"  [*] Saved {len(intermediate)} jobs to cache (incremental)")

    if overall_quality_metrics["rejected"]:
        logger(
            f"✂ Batch quality gate kept {overall_quality_metrics['accepted']} of {overall_quality_metrics['seen']} listings"
        )
        logger(
            "  Reasons: "
            + ", ".join(
                f"{reason}={count}"
                for reason, count in sorted(overall_quality_metrics["reasons"].items())
            )
        )

    logger(f"Running strict quality gate on {len(all_jobs)} aggregated job listings...")
    all_jobs, final_quality_metrics = _apply_quality_stage(all_jobs, quality_config, stage="final")
    if final_quality_metrics["rejected"]:
        logger(
            f"  ✂ Strict quality gate removed {final_quality_metrics['rejected']} listings before dedupe"
        )
        logger(
            "  Reasons: "
            + ", ".join(
                f"{reason}={count}"
                for reason, count in sorted(final_quality_metrics["reasons"].items())
            )
        )

    # Final deduplication
    logger(f"\n{'─'*50}")
    logger(f"Final deduplication of {len(all_jobs)} total job listings...")
    unique_jobs = deduplicate_jobs(all_jobs)
    logger(f"After deduplication: {len(unique_jobs)} unique jobs")

    # Description enrichment (fetch full descriptions for thin DDG snippets)
    if ENRICHER_AVAILABLE:
        logger(f"\nEnriching job descriptions...")
        unique_jobs = enrich_job_descriptions(
            unique_jobs,
            max_workers=8,
            min_description_length=200,
            logger=logger,
        )

    # Verify URLs are still alive
    logger(f"Verifying {len(unique_jobs)} job URLs...")
    verify_workers = max(1, int(scraping_config.get("url_verify_workers", 10)))
    unique_jobs = verify_job_urls(
        unique_jobs,
        logger=logger,
        max_workers=verify_workers,
        scraping_config=scraping_config,
    )
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
    quality_config = dict(config.get("quality", {}))
    jobs = [JobPosting.from_dict(j) for j in cache.get("jobs", [])]
    jobs, _ = _apply_quality_stage(jobs, quality_config, stage="final")
    return deduplicate_jobs(jobs)


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
