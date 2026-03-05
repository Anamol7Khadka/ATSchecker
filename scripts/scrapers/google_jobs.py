"""
google_jobs.py — Google Search aggregation scraper.
Uses googlesearch-python to find job postings across LinkedIn, StepStone, Indeed, XING, etc.
"""

import time
import re
from datetime import datetime
from typing import List

from scrapers.base import BaseScraper, JobPosting

try:
    from googlesearch import search as google_search
except ImportError:
    google_search = None


class GoogleJobsScraper(BaseScraper):
    name = "Google Jobs"

    SITE_FILTERS = [
        "site:linkedin.com/jobs",
        "site:stepstone.de",
        "site:indeed.de OR site:de.indeed.com",
        "site:xing.com/jobs",
        "site:jobteaser.com",
    ]

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        if google_search is None:
            print(f"[{self.name}] googlesearch-python not installed, skipping.")
            return []

        jobs = []
        try:
            # Build queries combining job types, keywords, and city (no early slicing)
            for jt in job_types:
                for kw in keywords:
                    query = f'{jt} {kw} {city} Germany'

                    # Also search with site filters for broader coverage
                    site_query = f'{query} ({" OR ".join(self.SITE_FILTERS[:3])})'

                    try:
                        results = list(
                            google_search(
                                site_query,
                                num_results=20,
                                lang="en",
                            )
                        )
                    except Exception:
                        # Fall back to simpler query
                        try:
                            results = list(
                                google_search(query, num_results=20, lang="en")
                            )
                        except Exception as e2:
                            print(f"[{self.name}] Search failed for '{query}': {e2}")
                            continue

                    for url in results:
                        if not isinstance(url, str):
                            continue

                        # Extract source from URL
                        source_detail = self._detect_source(url)
                        if not source_detail:
                            continue  # Skip non-job URLs

                        # Extract title from URL (best effort)
                        title = self._extract_title_from_url(url, jt, kw)

                        # Avoid duplicates
                        if any(j.url == url for j in jobs):
                            continue

                        jobs.append(
                            JobPosting(
                                title=title,
                                company="(via Google Search)",
                                location=city,
                                url=url,
                                description=f"Found via Google: {jt} {kw} in {city}",
                                source=f"Google→{source_detail}",
                                job_type=jt,
                                posted_date=datetime.now().isoformat(),
                            )
                        )

                        if len(jobs) >= self.max_results:
                            break

                    time.sleep(self.delay)

                    if len(jobs) >= self.max_results:
                        break
                if len(jobs) >= self.max_results:
                    break

        except Exception as e:
            print(f"[{self.name}] Error: {e}")

        return jobs

    @staticmethod
    def _detect_source(url: str) -> str:
        """Detect which job board a URL belongs to."""
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
        return ""

    @staticmethod
    def _extract_title_from_url(url: str, job_type: str, keyword: str) -> str:
        """Best-effort title extraction from URL path segments."""
        try:
            from urllib.parse import urlparse, unquote
            path = unquote(urlparse(url).path)
            # Common patterns: /jobs/view/title-here or /stellenangebot/title-here
            segments = [s for s in path.split("/") if s and len(s) > 5]
            if segments:
                # Take the longest segment as likely title
                title_segment = max(segments, key=len)
                title = title_segment.replace("-", " ").replace("_", " ").strip()
                # Capitalize words
                title = " ".join(w.capitalize() for w in title.split())
                return title[:100]  # Truncate
        except Exception:
            pass
        return f"{job_type} — {keyword}"
