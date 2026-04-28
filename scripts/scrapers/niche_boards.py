"""
niche_boards.py — Profile-aware niche job board scraper.

Targets domain-specific job boards based on the active profile's focus:
- Engineering: ingenieur.de, VDI, get-in-engineering.de, automotive-topjobs.de
- Tech: germantechjobs.de, berlinstartupjobs.com, honeypot.io
- Academic: academics.de, euraxess, researchgate

Uses DDG/Google site: queries to find relevant postings on these niche boards.
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
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
    warnings.filterwarnings("ignore", message=r"This package .* renamed to `ddgs`.*")
    try:
        from duckduckgo_search import DDGS
        DDG_AVAILABLE = True
    except ImportError:
        DDG_AVAILABLE = False


BOARD_PROFILES = {
    "engineering": [
        ("ingenieur.de", "Ingenieur.de"),
        ("vdi-nachrichten.com", "VDI"),
        ("get-in-engineering.de", "GetInEngineering"),
        ("automotive-topjobs.de", "AutomotiveTopJobs"),
        ("ingenieurweb.de", "IngenieurWeb"),
        ("cesar.de", "CESAR"),
    ],
    "tech": [
        ("germantechjobs.de", "GermanTechJobs"),
        ("berlinstartupjobs.com", "BerlinStartup"),
        ("honeypot.io", "Honeypot"),
        ("talent.io", "TalentIO"),
        ("4scotty.com", "4Scotty"),
        ("get-in-it.de", "GetInIT"),
    ],
    "academic": [
        ("academics.de", "Academics"),
        ("euraxess.ec.europa.eu", "EURAXESS"),
        ("researchgate.net/jobs", "ResearchGate"),
        ("daad.de", "DAAD"),
    ],
}

# These boards apply to ALL profiles
UNIVERSAL_BOARDS = [
    ("absolventa.de", "Absolventa"),
    ("campusjaeger.de", "Campusjäger"),
    ("squeaker.net", "Squeaker"),
]


class NicheBoardsScraper(BaseScraper):
    name = "NicheBoards"

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        if not DDG_AVAILABLE:
            self.log(f"[{self.name}] DDG not available, skipping.")
            return []

        jobs = []
        seen_urls = set()
        boards = self._select_boards()
        max_per_board = max(5, self.max_results // len(boards)) if boards else 10

        for domain, board_name in boards:
            board_jobs = self._search_board(
                domain, board_name, city, keywords, job_types, seen_urls, max_per_board
            )
            jobs.extend(board_jobs)

            if len(jobs) >= self.max_results:
                break

        self.log(f"[{self.name}] Total: {len(jobs)} jobs from {len(boards)} niche boards for {city}")
        return jobs

    def _select_boards(self) -> List[tuple]:
        """Select boards based on profile's niche_board_focus."""
        boards = list(UNIVERSAL_BOARDS)

        focus = "tech"  # default
        hints = self.config.get("scraper_hints", {})
        if isinstance(hints, dict):
            focus = hints.get("niche_board_focus", "tech")

        # Add focus-specific boards
        if focus in BOARD_PROFILES:
            boards.extend(BOARD_PROFILES[focus])
        else:
            # Unknown focus — add all
            for board_list in BOARD_PROFILES.values():
                boards.extend(board_list)

        return list(dict.fromkeys(boards))

    def _search_board(
        self, domain: str, board_name: str, city: str,
        keywords: List[str], job_types: List[str],
        seen_urls: set, max_results: int
    ) -> List[JobPosting]:
        jobs = []

        for kw in keywords[:4]:
            for jt in job_types[:3]:
                if not can_query("duckduckgo", self.config):
                    break

                query = f"site:{domain} {jt} {kw} {city}"

                try:
                    results = self._search_ddg(query, 10)
                    record_success("duckduckgo", self.config)
                except Exception as exc:
                    exc_str = str(exc)
                    if is_rate_limit_error(exc) or "SendRequest" in exc_str or "broken pipe" in exc_str.lower() or "connection error" in exc_str.lower() or "timed out" in exc_str.lower() or "connecttimeout" in exc_str.lower():
                        record_rate_limit("duckduckgo", self.config, reason=exc_str)
                        self.log(f"[{self.name}] DDG rate limited / connection dropped for {board_name} (timeout/limit)")
                        return jobs
                    self.log(f"[{self.name}] DDG error for {board_name}: {exc}")
                    continue

                for r in results:
                    url = r.get("url", "")
                    title = r.get("title", "")
                    snippet = r.get("snippet", "")

                    if not url or url in seen_urls:
                        continue
                    if domain not in url.lower():
                        continue
                    if is_listing_page(title, url):
                        continue

                    seen_urls.add(url)
                    jobs.append(
                        JobPosting(
                            title=title or f"{jt} {kw}",
                            company=f"(via {board_name})",
                            location=city,
                            url=url,
                            description=snippet[:500],
                            source=f"{self.name}→{board_name}",
                            job_type=jt,
                            posted_date=datetime.now().isoformat(),
                        )
                    )

                    if len(jobs) >= max_results:
                        return jobs

                self._polite_pause()

        return jobs

    def _search_ddg(self, query: str, max_results: int) -> List[Dict[str, str]]:
        with DDGS() as ddgs:
            rows = list(ddgs.text(query, max_results=max_results, region="de-de"))
        return [
            {
                "url": r.get("href", "") or r.get("link", ""),
                "title": r.get("title", ""),
                "snippet": r.get("body", ""),
            }
            for r in rows
        ]

    def _polite_pause(self):
        jitter = float(self.config.get("request_jitter_seconds", 1.0))
        time.sleep(self.delay + random.uniform(0, jitter))
