"""
company_portals.py — Broad web discovery scraper for company and university portals.
Searches direct career pages and less-common postings via multiple search engines.
"""

import random
import time
import warnings
from datetime import datetime
from typing import Dict, List
from urllib.parse import urlparse

from scrapers.base import BaseScraper, JobPosting, is_listing_page
from scrapers.rate_limiter import (
    can_query,
    get_engine_snapshot,
    is_rate_limit_error,
    record_rate_limit,
    record_success,
)

try:
    from ddgs import DDGS  # type: ignore[import-not-found]
    DDG_AVAILABLE = True
except ImportError:
    warnings.filterwarnings("ignore", message=r"This package .* renamed to `ddgs`.*")
    try:
        from duckduckgo_search import DDGS  # type: ignore[import-not-found]
        DDG_AVAILABLE = True
    except ImportError:
        DDG_AVAILABLE = False

try:
    from googlesearch import search as google_search  # type: ignore[import-not-found]
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False


CAREER_PATH_HINTS = (
    "career",
    "careers",
    "job",
    "jobs",
    "vacancy",
    "join-us",
    "positions",
    "stellen",
    "stellenangebot",
    "karriere",
)

NOISE_URL_HINTS = (
    "linkedin.com/in/",
    "xing.com/profile",
    "/salaries/",
    "karrierebibel",
    "lebenslauf",
    "curriculum-vitae",
    "/skills/",
)

NOISE_TITLE_HINTS = (
    "salary:",
    "resume",
    "profile",
    "lebenslauf",
)


class CompanyPortalsScraper(BaseScraper):
    name = "CompanyPortals"

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        jobs: List[JobPosting] = []
        seen_urls = set()

        engines = self.config.get("search_engines", ["duckduckgo", "google"])
        domains = list(self.config.get("company_domains", []))
        if self.config.get("enable_university_discovery", True):
            domains.extend(self.config.get("university_portals", []))

        if not domains:
            return jobs

        max_keywords = int(self.config.get("max_keywords_per_city", 6))
        max_job_types = int(self.config.get("max_job_types", 6))
        max_per_query = min(12, max(4, self.max_results // 8))

        for domain in domains:
            for jt in job_types[:max_job_types]:
                for kw in keywords[:max_keywords]:
                    query = self._build_portal_query(domain, city, jt, kw)
                    discovered = []
                    available_engines = []

                    if "duckduckgo" in engines and DDG_AVAILABLE and can_query("duckduckgo", self.config):
                        available_engines.append("duckduckgo")
                    if "google" in engines and GOOGLE_AVAILABLE and can_query("google", self.config):
                        available_engines.append("google")

                    if not available_engines:
                        self._log_engine_skip("google" if "google" in engines else "duckduckgo", domain)
                        self._polite_pause()
                        continue

                    if "duckduckgo" in available_engines:
                        discovered.extend(
                            self._run_with_retry(
                                lambda: self._search_duckduckgo(query, max_per_query),
                                label=f"ddg:{domain}",
                                engine="duckduckgo",
                            )
                        )

                    if "google" in available_engines:
                        discovered.extend(
                            self._run_with_retry(
                                lambda: self._search_google(query, max_per_query),
                                label=f"google:{domain}",
                                engine="google",
                            )
                        )

                    for item in discovered:
                        url = item.get("url", "").strip()
                        title = item.get("title", "").strip()
                        snippet = item.get("snippet", "").strip()
                        engine = item.get("engine", "web")

                        if not url or url in seen_urls:
                            continue
                        if domain not in url.lower():
                            continue
                        if is_listing_page(title, url):
                            continue
                        if not self._looks_like_job_or_career(url, title, snippet):
                            continue

                        seen_urls.add(url)
                        jobs.append(
                            JobPosting(
                                title=title or f"{jt} {kw}",
                                company=self._company_from_domain(domain),
                                location=city,
                                url=url,
                                description=snippet[:700],
                                source=f"{self.name}→{engine}",
                                job_type=jt,
                                posted_date=datetime.now().isoformat(),
                            )
                        )

                    if len(jobs) >= self.max_results:
                        return jobs

                    self._polite_pause()

        return jobs

    def _build_portal_query(self, domain: str, city: str, job_type: str, keyword: str) -> str:
        # Include both English and German career terms to broaden reach.
        return (
            f"site:{domain} ({job_type}) ({keyword}) ({city} Germany) "
            f"(careers OR jobs OR karriere OR stellenangebote OR thesis OR masterarbeit)"
        )

    def _search_duckduckgo(self, query: str, max_results: int) -> List[Dict[str, str]]:
        try:
            with DDGS(timeout=30) as ddgs:
                rows = list(ddgs.text(query, max_results=max_results, region="de-de"))
        except TypeError:
            # Fallback for older DDGS versions without timeout support
            with DDGS() as ddgs:
                rows = list(ddgs.text(query, max_results=max_results, region="de-de"))

        out = []
        for r in rows:
            out.append(
                {
                    "url": r.get("href", "") or r.get("link", ""),
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "engine": "DDG",
                }
            )
        return out

    def _search_google(self, query: str, max_results: int) -> List[Dict[str, str]]:
        out = []
        for url in google_search(query, num_results=max_results, sleep_interval=0.4):
            out.append(
                {
                    "url": url,
                    "title": "",
                    "snippet": "",
                    "engine": "Google",
                }
            )
        return out

    def _run_with_retry(self, fn, label: str, engine: str) -> List[Dict[str, str]]:
        if not can_query(engine, self.config):
            self._log_engine_skip(engine, label)
            return []

        retries = int(self.config.get("max_retries_per_task", 3))
        base = float(self.config.get("retry_backoff_base_seconds", 1.0))
        cap = float(self.config.get("retry_backoff_max_seconds", 20.0))
        jitter = float(self.config.get("request_jitter_seconds", 1.0))

        last_error = None
        for attempt in range(retries + 1):
            try:
                results = fn() or []
                record_success(engine, self.config)
                return results
            except Exception as exc:
                last_error = exc
                if is_rate_limit_error(exc):
                    cooldown = record_rate_limit(engine, self.config, reason=str(exc))
                    if cooldown > 0:
                        self.log(f"[{self.name}] {engine} cooldown {int(cooldown)}s for {label}: {exc}")
                    else:
                        self.log(f"[{self.name}] {engine} rate-limit warning for {label}: {exc}")
                    return []
                if attempt >= retries:
                    break
                wait = min(cap, base * (2 ** attempt)) + random.uniform(0, jitter)
                self.log(f"[{self.name}] Retry {attempt + 1}/{retries} for {label} after error: {exc}")
                time.sleep(wait)

        if last_error:
            self.log(f"[{self.name}] Giving up {label}: {last_error}")
        return []

    def _log_engine_skip(self, engine: str, label: str):
        snapshot = get_engine_snapshot(engine)
        remaining = snapshot.get("remaining_seconds", 0)
        self.log(f"[{self.name}] Skipping {engine} for {label} due to cooldown ({remaining}s remaining)")

    def _polite_pause(self):
        jitter = float(self.config.get("request_jitter_seconds", 1.0))
        time.sleep(self.delay + random.uniform(0, jitter))

    @staticmethod
    def _company_from_domain(domain: str) -> str:
        root = domain.split(".")[0]
        return root.replace("-", " ").title()

    @staticmethod
    def _looks_like_job_or_career(url: str, title: str, snippet: str) -> bool:
        hay = f"{url} {title} {snippet}".lower()

        if any(h in hay for h in NOISE_URL_HINTS):
            return False
        if any(h in (title or "").lower() for h in NOISE_TITLE_HINTS):
            return False

        if any(h in hay for h in CAREER_PATH_HINTS):
            return True

        # Many university/research pages do not include classic career paths.
        netloc = urlparse(url).netloc.lower()
        if any(x in netloc for x in ("ovgu", "fraunhofer", "daad", "dlr")):
            return True

        return False
