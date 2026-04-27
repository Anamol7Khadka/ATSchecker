"""
lever_api.py — Direct Lever Job Board API scraper.

Lever exposes a PUBLIC, UNAUTHENTICATED JSON API at:
    https://api.lever.co/v0/postings/{company}?mode=json

Returns ALL jobs for a company with descriptions, categories, and apply URLs.
"""

import json
import os
import re
import time
from html import unescape
from typing import List

import requests

from scrapers.base import BaseScraper, JobPosting

TAG_RE = re.compile(r"<[^>]+>")


class LeverAPIScraper(BaseScraper):
    name = "LeverAPI"

    API_BASE = "https://api.lever.co/v0/postings"

    # Known companies using Lever for German/European hiring
    DEFAULT_SLUGS = [
        "checkout",
        "zenjob",
        "omio",
        "adjust",
        "raisin",
        "unu-motors",
        "pitch",
        "moss",
        "taxfix",
        "comtravo",
        "grover",
        "pleo",
    ]

    SLUG_CACHE_FILE = ".lever_slugs.json"

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        jobs = []
        seen_urls = set()
        slugs = self._get_slugs()

        for slug in slugs:
            try:
                api_url = f"{self.API_BASE}/{slug}?mode=json"
                resp = requests.get(api_url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ATSchecker/2.0)",
                    "Accept": "application/json",
                })
                if resp.status_code != 200:
                    continue

                postings = resp.json()
                if not isinstance(postings, list):
                    continue

                for posting in postings:
                    title = posting.get("text", "")
                    categories = posting.get("categories", {})
                    location = categories.get("location", "") if isinstance(categories, dict) else ""
                    hosted_url = posting.get("hostedUrl", "")
                    desc_plain = posting.get("descriptionPlain", "")
                    created_at = posting.get("createdAt", "")

                    if not title or not hosted_url:
                        continue
                    if hosted_url in seen_urls:
                        continue

                    # Filter by city
                    if not self._matches_city(location, city):
                        continue

                    # Filter by keyword relevance
                    haystack = f"{title} {desc_plain}".lower()
                    if not any(kw.lower() in haystack for kw in keywords[:8]):
                        continue

                    seen_urls.add(hosted_url)
                    company = slug.replace("-", " ").title()

                    # Parse created_at from epoch ms
                    posted_date = ""
                    if created_at:
                        try:
                            from datetime import datetime
                            ts = int(created_at) / 1000 if isinstance(created_at, (int, float)) else 0
                            if ts > 0:
                                posted_date = datetime.fromtimestamp(ts).isoformat()
                        except Exception:
                            posted_date = str(created_at)

                    jobs.append(
                        JobPosting(
                            title=title,
                            company=company,
                            location=location or city,
                            url=hosted_url,
                            description=desc_plain[:5000],
                            source=self.name,
                            job_type=self._detect_job_type(title, desc_plain, job_types),
                            posted_date=posted_date,
                        )
                    )

                    if len(jobs) >= self.max_results:
                        break

                if postings:
                    self.log(f"[{self.name}] {slug}: {len(postings)} total, matched for {city}")

            except Exception as e:
                self.log(f"[{self.name}] Error for {slug}: {e}")

            time.sleep(0.3)

            if len(jobs) >= self.max_results:
                break

        self.log(f"[{self.name}] Total: {len(jobs)} jobs from {len(slugs)} companies for {city}")
        return jobs

    def _get_slugs(self) -> List[str]:
        slugs = list(self.DEFAULT_SLUGS)
        hints = self.config.get("scraper_hints", {})
        if isinstance(hints, dict):
            extra = hints.get("lever_slugs", [])
            if isinstance(extra, list):
                slugs.extend(extra)

        cache_path = self.config.get("_project_root", "")
        if cache_path:
            cache_file = os.path.join(cache_path, self.SLUG_CACHE_FILE)
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r") as f:
                        cached = json.load(f)
                        if isinstance(cached, list):
                            slugs.extend(cached)
                except Exception:
                    pass

        return list(dict.fromkeys(slugs))

    def _matches_city(self, location: str, target_city: str) -> bool:
        if not location:
            return True
        loc_lower = location.lower()
        city_lower = target_city.lower()
        if city_lower in loc_lower:
            return True
        aliases = {
            "münchen": ["munich", "muenchen"],
            "munich": ["münchen", "muenchen"],
        }
        for alias in aliases.get(city_lower, []):
            if alias in loc_lower:
                return True
        if any(t in loc_lower for t in ("germany", "deutschland", "remote", "dach", "europe")):
            return True
        return False

    def _detect_job_type(self, title: str, description: str, job_types: List[str]) -> str:
        haystack = f"{title} {description}".lower()
        for jt in job_types:
            if jt.lower() in haystack:
                return jt
        return ""

    @staticmethod
    def _clean_html(html: str) -> str:
        text = unescape(html or "")
        text = TAG_RE.sub(" ", text)
        return re.sub(r"\s+", " ", text).strip()
