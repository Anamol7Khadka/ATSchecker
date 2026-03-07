"""
google_jobs.py — Search aggregation scraper using DuckDuckGo.
Uses duckduckgo_search to find job postings across LinkedIn, StepStone, Indeed, XING, etc.
DuckDuckGo is far more tolerant of automated queries than Google.
"""

import time
from datetime import datetime
from typing import List

from scrapers.base import BaseScraper, JobPosting, is_listing_page

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False


class GoogleJobsScraper(BaseScraper):
    name = "SearchAggregator"

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
    ]

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        if not DDG_AVAILABLE:
            print(f"[{self.name}] duckduckgo_search not installed, skipping.")
            return []

        jobs = []

        try:
            for jt in job_types[:6]:
                for kw in keywords[:6]:
                    query = f"{jt} {kw} {city} Germany job"

                    try:
                        with DDGS() as ddgs:
                            results = list(ddgs.text(query, max_results=20, region="de-de"))
                    except Exception as e:
                        print(f"[{self.name}] DuckDuckGo search failed for '{query}': {e}")
                        time.sleep(3)
                        continue

                    for r in results:
                        url = r.get("href", "") or r.get("link", "")
                        title = r.get("title", "")
                        snippet = r.get("body", "")

                        if not url or not isinstance(url, str):
                            continue

                        # Check if it's a job board URL
                        source_detail = self._detect_source(url)
                        if not source_detail:
                            continue

                        # Skip search/listing pages — we only want individual jobs
                        if is_listing_page(title, url):
                            continue

                        # Use search result title if available, else extract from URL
                        if not title or len(title) < 5:
                            title = self._extract_title_from_url(url, jt, kw)

                        if any(j.url == url for j in jobs):
                            continue

                        jobs.append(
                            JobPosting(
                                title=title,
                                company=f"(via {source_detail})",
                                location=city,
                                url=url,
                                description=snippet[:500] if snippet else f"Found via search: {jt} {kw} in {city}",
                                source=f"DDG→{source_detail}",
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
        return ""

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
