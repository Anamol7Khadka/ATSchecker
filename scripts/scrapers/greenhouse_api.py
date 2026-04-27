"""
greenhouse_api.py — Direct Greenhouse Job Board API scraper.

Greenhouse exposes a PUBLIC, UNAUTHENTICATED JSON API at:
    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Returns ALL jobs for a company with full HTML descriptions.
No rate limits, no anti-bot, no JavaScript rendering needed.
"""

import json
import os
import re
import time
from datetime import datetime
from html import unescape
from typing import List, Optional
from urllib.parse import urlparse

import requests

from scrapers.base import BaseScraper, JobPosting

TAG_RE = re.compile(r"<[^>]+>")


class GreenhouseAPIScraper(BaseScraper):
    name = "GreenhouseAPI"

    API_BASE = "https://boards-api.greenhouse.io/v1/boards"

    # Known German tech companies using Greenhouse (auto-discovered slugs get added)
    DEFAULT_SLUGS = [
        "celonis",
        "personio",
        "contentful",
        "hellofreshgroup",
        "n26",
        "ecosia",
        "flixbus",
        "gorillas",
        "deliveryhero",
        "trade-republic",
        "scalablecapital",
        "sumup",
        "sennder",
        "forto",
        "agile-robots",
    ]

    SLUG_CACHE_FILE = ".greenhouse_slugs.json"

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        jobs = []
        seen_urls = set()
        slugs = self._get_slugs()

        for slug in slugs:
            try:
                api_url = f"{self.API_BASE}/{slug}/jobs?content=true"
                resp = requests.get(api_url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ATSchecker/2.0)",
                    "Accept": "application/json",
                })
                if resp.status_code != 200:
                    continue

                data = resp.json()
                api_jobs = data.get("jobs", [])

                for job_data in api_jobs:
                    title = job_data.get("title", "")
                    location_obj = job_data.get("location", {})
                    location_name = location_obj.get("name", "") if isinstance(location_obj, dict) else str(location_obj)
                    absolute_url = job_data.get("absolute_url", "")
                    content_html = job_data.get("content", "")
                    updated_at = job_data.get("updated_at", "")

                    if not title or not absolute_url:
                        continue
                    if absolute_url in seen_urls:
                        continue

                    # Filter by city relevance
                    if not self._matches_city(location_name, city):
                        continue

                    # Filter by keyword relevance (title or description must contain at least one)
                    description = self._clean_html(content_html)
                    haystack = f"{title} {description}".lower()
                    if not any(kw.lower() in haystack for kw in keywords[:8]):
                        continue

                    seen_urls.add(absolute_url)
                    company = slug.replace("-", " ").title()

                    jobs.append(
                        JobPosting(
                            title=title,
                            company=company,
                            location=location_name or city,
                            url=absolute_url,
                            description=description[:5000],
                            source=self.name,
                            job_type=self._detect_job_type(title, description, job_types),
                            posted_date=updated_at,
                        )
                    )

                    if len(jobs) >= self.max_results:
                        break

                if api_jobs:
                    self.log(f"[{self.name}] {slug}: {len(api_jobs)} total, {sum(1 for j in jobs if slug.replace('-', ' ').title() == j.company)} matched for {city}")

            except requests.exceptions.Timeout:
                self.log(f"[{self.name}] Timeout for {slug}")
            except Exception as e:
                self.log(f"[{self.name}] Error for {slug}: {e}")

            # Brief pause between companies
            time.sleep(0.3)

            if len(jobs) >= self.max_results:
                break

        self.log(f"[{self.name}] Total: {len(jobs)} jobs from {len(slugs)} companies for {city}")
        return jobs

    def _get_slugs(self) -> List[str]:
        """Get all known Greenhouse company slugs."""
        slugs = list(self.DEFAULT_SLUGS)

        # Add profile-specific slugs from config
        hints = self.config.get("scraper_hints", {})
        if isinstance(hints, dict):
            extra = hints.get("greenhouse_slugs", [])
            if isinstance(extra, list):
                slugs.extend(extra)

        # Load cached auto-discovered slugs
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

        return list(dict.fromkeys(slugs))  # dedupe preserving order

    def _matches_city(self, location: str, target_city: str) -> bool:
        """Check if job location matches the target city."""
        if not location:
            return True  # Include jobs with no location specified
        loc_lower = location.lower()
        city_lower = target_city.lower()

        # Direct match
        if city_lower in loc_lower:
            return True

        # Common aliases
        aliases = {
            "münchen": ["munich", "muenchen"],
            "munich": ["münchen", "muenchen"],
            "köln": ["cologne", "koeln"],
            "nürnberg": ["nuremberg", "nuernberg"],
        }
        for alias in aliases.get(city_lower, []):
            if alias in loc_lower:
                return True

        # "Germany" or "Remote" jobs match any German city
        if any(term in loc_lower for term in ("germany", "deutschland", "remote", "dach")):
            return True

        return False

    def _detect_job_type(self, title: str, description: str, job_types: List[str]) -> str:
        """Try to detect job type from title/description."""
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
