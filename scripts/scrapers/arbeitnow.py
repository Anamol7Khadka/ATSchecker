"""
arbeitnow.py — Arbeitnow API scraper (free, no API key needed).
Germany-focused, English-language tech jobs.
"""

import time
from typing import List

import requests

from scrapers.base import BaseScraper, JobPosting


class ArbeitnowScraper(BaseScraper):
    name = "Arbeitnow"
    BASE_URL = "https://www.arbeitnow.com/api/job-board-api"

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        jobs = []
        try:
            page = 1
            while len(jobs) < self.max_results and page <= 15:
                params = {"page": page}
                resp = requests.get(self.BASE_URL, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()

                postings = data.get("data", [])
                if not postings:
                    break

                for item in postings:
                    title = item.get("title", "")
                    location = item.get("location", "")
                    description = item.get("description", "")
                    company = item.get("company_name", "")
                    url = item.get("url", "")
                    tags = item.get("tags", [])
                    created = item.get("created_at", "")

                    # Filter by city
                    city_lower = city.lower()
                    if city_lower not in location.lower() and city_lower not in title.lower():
                        continue

                    # Filter by keywords/job types (loose matching)
                    combined_text = f"{title} {description} {' '.join(tags)}".lower()
                    keyword_match = any(kw.lower() in combined_text for kw in keywords)
                    type_match = any(jt.lower() in combined_text for jt in job_types)

                    if keyword_match or type_match:
                        # Detect job type
                        detected_type = ""
                        for jt in job_types:
                            if jt.lower() in combined_text:
                                detected_type = jt
                                break

                        jobs.append(
                            JobPosting(
                                title=title,
                                company=company,
                                location=location,
                                url=url,
                                description=self._clean_html(description)[:2000],
                                posted_date=created,
                                source=self.name,
                                job_type=detected_type,
                                tags=tags,
                            )
                        )

                    if len(jobs) >= self.max_results:
                        break

                page += 1
                time.sleep(self.delay)

        except Exception as e:
            print(f"[{self.name}] Error scraping {city}: {e}")

        return jobs

    @staticmethod
    def _clean_html(text: str) -> str:
        """Remove HTML tags from description."""
        import re
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean
