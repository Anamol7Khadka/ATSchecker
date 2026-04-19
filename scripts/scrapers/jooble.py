"""
jooble.py - Jooble Germany scraper using their free API.
Jooble is one of the largest job aggregators, covering Indeed, LinkedIn,
StepStone, Glassdoor and hundreds of other sites across Germany.
Free API key at https://jooble.org/api/about
"""

import time
from datetime import datetime
from typing import List

import requests

from scrapers.base import BaseScraper, JobPosting


class JoobleScraper(BaseScraper):
    name = "Jooble"
    BASE_URL = "https://jooble.org/api/"

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        api_key = self.config.get("jooble_api_key", "")

        if not api_key:
            self.log(
                f"[{self.name}] No API key configured. "
                "Register free at https://jooble.org/api/about "
                "and add jooble_api_key to config.yaml under 'scraping:'."
            )
            return []

        jobs = []
        seen_urls = set()

        try:
            for jt in job_types[:4]:
                for kw in keywords[:6]:
                    query = f"{jt} {kw}"

                    for page in range(1, 6):  # Up to 5 pages
                        payload = {
                            "keywords": query,
                            "location": f"{city}, Germany",
                            "page": str(page),
                            "searchMode": "1",  # 1 = relevance within location
                        }

                        try:
                            resp = requests.post(
                                f"{self.BASE_URL}{api_key}",
                                json=payload,
                                headers={"Content-Type": "application/json"},
                                timeout=15,
                            )
                            if resp.status_code == 429:
                                self.log(f"[{self.name}] Rate limited, pausing...")
                                time.sleep(10)
                                continue
                            resp.raise_for_status()
                        except requests.RequestException as e:
                            self.log(f"[{self.name}] API request failed: {e}")
                            break

                        data = resp.json()
                        results = data.get("jobs", [])
                        if not results:
                            break

                        for item in results:
                            title = item.get("title", "").strip()
                            company = item.get("company", "").strip()
                            location = item.get("location", city).strip()
                            job_url = item.get("link", "")
                            snippet = item.get("snippet", "")
                            updated = item.get("updated", "")
                            salary = item.get("salary", "")
                            job_type_str = item.get("type", "")

                            if not title or not job_url:
                                continue
                            if job_url in seen_urls:
                                continue
                            seen_urls.add(job_url)

                            # Clean HTML from snippet
                            description = self._clean_html(snippet)

                            posted_date = ""
                            if updated:
                                try:
                                    posted_date = datetime.fromisoformat(
                                        updated.replace("Z", "+00:00")
                                    ).isoformat()
                                except (ValueError, TypeError):
                                    posted_date = updated

                            jobs.append(
                                JobPosting(
                                    title=title,
                                    company=company,
                                    location=location,
                                    url=job_url,
                                    description=description[:2000],
                                    source=self.name,
                                    job_type=job_type_str or jt,
                                    salary=salary if salary else None,
                                    posted_date=posted_date,
                                )
                            )

                        if len(jobs) >= self.max_results:
                            break
                        time.sleep(1)

                    if len(jobs) >= self.max_results:
                        break
                if len(jobs) >= self.max_results:
                    break

        except Exception as e:
            self.log(f"[{self.name}] Error: {e}")

        self.log(f"[{self.name}] Found {len(jobs)} jobs for {city}")
        return jobs

    @staticmethod
    def _clean_html(text: str) -> str:
        import re
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = re.sub(r"&\w+;", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean
