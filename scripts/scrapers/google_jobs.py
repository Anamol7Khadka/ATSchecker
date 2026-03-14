"""
google_jobs.py — Broad search aggregation scraper.
Uses multiple engines (DuckDuckGo + Google when available) to discover jobs
across major boards and less obvious company/university pages.
"""

import random
import time
import warnings
from datetime import datetime
from typing import List
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
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
    warnings.filterwarnings("ignore", message=r"This package .* renamed to `ddgs`.*")
    try:
        from duckduckgo_search import DDGS
        DDG_AVAILABLE = True
    except ImportError:
        DDG_AVAILABLE = False

try:
    from googlesearch import search as google_search
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False


class GoogleJobsScraper(BaseScraper):
    name = "SearchAggregator"

    NOISE_URL_HINTS = [
        "linkedin.com/in/",
        "xing.com/profile",
        "/salaries/",
        "karrierebibel",
        "lebenslauf",
        "curriculum-vitae",
        "/skills/",
    ]

    NOISE_TITLE_HINTS = [
        "salary:",
        "resume",
        "lebenslauf",
        "profile",
    ]

    JOB_SITES = [
        "linkedin.com/jobs",
        "stepstone.de",
        "indeed.de",
        "de.indeed.com",
        "xing.com/jobs",
        "jobteaser.com",
        "glassdoor.de",
        "monster.de",
        "karriere.de",
        "jobs.meinestadt.de",
        "greenjobs.de",
        "jobware.de",
        "yourfirm.de",
        "academics.de",
        "get-in-engineering.de",
        "researchgate.net/jobs",
        "ovgu.de",
        "fraunhofer.de",
        "dlr.de",
    ]

    QUERY_TEMPLATES = [
        "{jt} {kw} {city} Germany job",
        "{jt} {kw} {city} Germany career",
        "{jt} {kw} {city} Germany thesis",
        "{kw} {city} Germany machine learning engineer",
        "{kw} {city} Germany data science working student",
    ]

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        engines = self.config.get("search_engines", ["duckduckgo"])
        if "duckduckgo" in engines and not DDG_AVAILABLE and "google" not in engines:
            print(f"[{self.name}] No available engine backend, skipping.")
            return []
        if "google" in engines and not GOOGLE_AVAILABLE and "duckduckgo" not in engines:
            print(f"[{self.name}] No available engine backend, skipping.")
            return []

        jobs = []
        seen_urls = set()
        max_keywords = int(self.config.get("max_keywords_per_city", 6))
        max_job_types = int(self.config.get("max_job_types", 6))
        max_per_query = 20

        try:
            for jt in job_types[:max_job_types]:
                for kw in keywords[:max_keywords]:
                    for tmpl in self.QUERY_TEMPLATES:
                        query = tmpl.format(jt=jt, kw=kw, city=city)
                        results = []
                        available_engines = []

                        if "duckduckgo" in engines and DDG_AVAILABLE and can_query("duckduckgo", self.config):
                            available_engines.append("duckduckgo")
                        if "google" in engines and GOOGLE_AVAILABLE and can_query("google", self.config):
                            available_engines.append("google")

                        if not available_engines:
                            self._log_engine_skip("duckduckgo" if "duckduckgo" in engines else "google", query)
                            self._polite_pause()
                            continue

                        if "duckduckgo" in available_engines:
                            results.extend(
                                self._run_with_retry(
                                    lambda: self._search_duckduckgo(query, max_per_query),
                                    label=f"ddg:{query}",
                                    engine="duckduckgo",
                                )
                            )

                        if "google" in available_engines:
                            results.extend(
                                self._run_with_retry(
                                    lambda: self._search_google(query, max_per_query),
                                    label=f"google:{query}",
                                    engine="google",
                                )
                            )

                        for r in results:
                            url = r.get("url", "")
                            title = r.get("title", "")
                            snippet = r.get("snippet", "")
                            engine = r.get("engine", "Web")

                            if not url or not isinstance(url, str):
                                continue
                            if url in seen_urls:
                                continue
                            if self._is_noise_result(url, title):
                                continue

                            source_detail = self._detect_source(url)
                            if not source_detail:
                                continue
                            if is_listing_page(title, url):
                                continue

                            if not title or len(title) < 5:
                                title = self._extract_title_from_url(url, jt, kw)

                            seen_urls.add(url)
                            jobs.append(
                                JobPosting(
                                    title=title,
                                    company=f"(via {source_detail})",
                                    location=city,
                                    url=url,
                                    description=snippet[:500] if snippet else f"Found via search: {jt} {kw} in {city}",
                                    source=f"{engine}→{source_detail}",
                                    job_type=jt,
                                    posted_date=datetime.now().isoformat(),
                                )
                            )

                            if len(jobs) >= self.max_results:
                                break

                        self._polite_pause()

                        if len(jobs) >= self.max_results:
                            break

                if len(jobs) >= self.max_results:
                    break

        except Exception as e:
            print(f"[{self.name}] Error: {e}")

        return jobs

    def _is_noise_result(self, url: str, title: str) -> bool:
        url_l = url.lower()
        title_l = (title or "").lower()

        if any(h in url_l for h in self.NOISE_URL_HINTS):
            return True
        if any(h in title_l for h in self.NOISE_TITLE_HINTS):
            return True

        return False

    def _search_duckduckgo(self, query: str, max_results: int):
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

    def _search_google(self, query: str, max_results: int):
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

    def _run_with_retry(self, fn, label: str, engine: str):
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
                        self.log(f"[{self.name}] {engine} cooldown {int(cooldown)}s after {label}: {exc}")
                    else:
                        self.log(f"[{self.name}] {engine} rate-limit warning for {label}: {exc}")
                    return []
                if attempt >= retries:
                    break
                wait = min(cap, base * (2 ** attempt)) + random.uniform(0, jitter)
                self.log(f"[{self.name}] Retry {attempt + 1}/{retries} for {label}: {exc}")
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
    def _detect_source(url: str) -> str:
        url_lower = url.lower()
        if "linkedin.com" in url_lower:
            return "LinkedIn"
        elif "stepstone.de" in url_lower:
            return "StepStone"
        elif "indeed" in url_lower:
            return "Indeed"
        elif "xing.com" in url_lower:
            return "XING"
        elif "jobteaser.com" in url_lower:
            return "Jobteaser"
        elif "glassdoor" in url_lower:
            return "Glassdoor"
        elif "monster" in url_lower:
            return "Monster"
        elif "karriere" in url_lower:
            return "Karriere"
        elif "meinestadt" in url_lower:
            return "MeineStadt"
        elif "ovgu.de" in url_lower:
            return "OVGU"
        elif "fraunhofer.de" in url_lower:
            return "Fraunhofer"
        elif "dlr.de" in url_lower:
            return "DLR"

        # Keep less-known company portals instead of dropping them.
        netloc = urlparse(url_lower).netloc
        if netloc and "." in netloc:
            root = netloc.split(".")[0].replace("www", "").strip("-")
            if root:
                return root.title()

        return "Web"

    @staticmethod
    def _extract_title_from_url(url: str, job_type: str, keyword: str) -> str:
        try:
            from urllib.parse import urlparse, unquote
            path = unquote(urlparse(url).path)
            segments = [s for s in path.split("/") if s and len(s) > 5]
            if segments:
                title_segment = max(segments, key=len)
                title = title_segment.replace("-", " ").replace("_", " ").strip()
                title = " ".join(w.capitalize() for w in title.split())
                return title[:100]
        except Exception:
            pass
        return f"{job_type} — {keyword}"
