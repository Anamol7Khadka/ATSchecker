"""
remoteok.py — RemoteOK job scraper using their free JSON API.
RemoteOK provides a free API at remoteok.com/api that returns all recent remote jobs.
No authentication required. Filters client-side by keywords and job_types.
"""

import time
from datetime import datetime
from typing import List

import requests

from scrapers.base import BaseScraper, JobPosting


class RemoteOKScraper(BaseScraper):
    name = "RemoteOK"
    API_URL = "https://remoteok.com/api"

    HEADERS = {
        "User-Agent": "ATSchecker/1.0 (Job Search Tool)",
        "Accept": "application/json",
    }

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        jobs = []

        try:
            resp = requests.get(self.API_URL, headers=self.HEADERS, timeout=20)
            if resp.status_code == 429:
                print(f"[{self.name}] Rate limited, skipping.")
                return []
            resp.raise_for_status()

            data = resp.json()
            # First element is metadata, skip it
            listings = data[1:] if len(data) > 1 else data

        except Exception as e:
            print(f"[{self.name}] API request failed: {e}")
            return []

        # Build search terms for client-side filtering
        search_terms = set()
        for jt in job_types[:6]:
            search_terms.add(jt.lower())
        for kw in keywords[:6]:
            search_terms.add(kw.lower())

        for item in listings:
            if not isinstance(item, dict):
                continue

            title = item.get("position", "").strip()
            company = item.get("company", "").strip()
            job_url = item.get("url", "") or item.get("apply_url", "")
            description = item.get("description", "")[:500]
            tags = item.get("tags", []) or []
            created = item.get("date", "") or item.get("epoch", "")
            salary = item.get("salary", "")
            location_val = item.get("location", "Remote")

            if not title or not job_url:
                continue

            # Ensure URL is absolute
            if job_url.startswith("/"):
                job_url = f"https://remoteok.com{job_url}"

            # Filter: check if any search term matches title, tags, or description
            searchable = f"{title} {company} {' '.join(tags)} {description}".lower()
            if not any(term in searchable for term in search_terms):
                continue

            if any(j.url == job_url for j in jobs):
                continue

            posted_date = ""
            if created:
                try:
                    if isinstance(created, (int, float)):
                        posted_date = datetime.fromtimestamp(created).isoformat()
                    else:
                        posted_date = datetime.fromisoformat(
                            str(created).replace("Z", "+00:00")
                        ).isoformat()
                except (ValueError, TypeError, OSError):
                    posted_date = str(created)

            jobs.append(
                JobPosting(
                    title=title,
                    company=company,
                    location=location_val if location_val else "Remote",
                    url=job_url,
                    description=description,
                    source=self.name,
                    job_type="Remote",
                    salary=salary if salary else None,
                    tags=tags[:10] if tags else [],
                    posted_date=posted_date,
                )
            )

            if len(jobs) >= self.max_results:
                break

        return jobs
