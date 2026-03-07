"""
adzuna.py — Adzuna Germany scraper using their free REST API.
Adzuna provides a free API for job listings in Germany and other countries.
Requires free API keys from https://developer.adzuna.com/
"""

import time
from datetime import datetime
from typing import List

import requests

from scrapers.base import BaseScraper, JobPosting


class AdzunaScraper(BaseScraper):
    name = "Adzuna"
    BASE_URL = "https://api.adzuna.com/v1/api/jobs/de/search"

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        app_id = self.config.get("adzuna_app_id", "")
        app_key = self.config.get("adzuna_app_key", "")

        if not app_id or not app_key:
            print(
                f"[{self.name}] No API keys configured. "
                "Register free at https://developer.adzuna.com/ "
                "and add adzuna_app_id / adzuna_app_key to config.yaml under 'scraping:'."
            )
            return []

        jobs = []

        try:
            for jt in job_types[:6]:
                for kw in keywords[:6]:
                    query = f"{jt} {kw}"

                    for page in range(1, 4):  # 3 pages max
                        params = {
                            "app_id": app_id,
                            "app_key": app_key,
                            "what": query,
                            "where": city,
                            "results_per_page": 50,
                            "page": page,
                            "sort_by": "date",
                            "content-type": "application/json",
                        }

                        try:
                            resp = requests.get(
                                f"{self.BASE_URL}/{page}",
                                params=params,
                                timeout=15,
                            )
                            if resp.status_code == 401:
                                print(f"[{self.name}] Invalid API keys. Check adzuna_app_id/adzuna_app_key in config.yaml.")
                                return jobs
                            if resp.status_code == 429:
                                print(f"[{self.name}] Rate limited, pausing...")
                                time.sleep(10)
                                continue
                            resp.raise_for_status()
                        except requests.RequestException as e:
                            print(f"[{self.name}] API request failed: {e}")
                            break

                        data = resp.json()
                        results = data.get("results", [])
                        if not results:
                            break

                        for item in results:
                            title = item.get("title", "").strip()
                            company = item.get("company", {}).get("display_name", "").strip()
                            location = item.get("location", {}).get("display_name", city).strip()
                            job_url = item.get("redirect_url", "")
                            description = item.get("description", "")[:500]
                            created = item.get("created", "")
                            salary_min = item.get("salary_min")
                            salary_max = item.get("salary_max")

                            if not title or not job_url:
                                continue

                            if any(j.url == job_url for j in jobs):
                                continue

                            salary = ""
                            if salary_min and salary_max:
                                salary = f"€{int(salary_min):,} - €{int(salary_max):,}"
                            elif salary_min:
                                salary = f"from €{int(salary_min):,}"

                            posted_date = ""
                            if created:
                                try:
                                    posted_date = datetime.fromisoformat(
                                        created.replace("Z", "+00:00")
                                    ).isoformat()
                                except (ValueError, TypeError):
                                    posted_date = created

                            jobs.append(
                                JobPosting(
                                    title=title,
                                    company=company,
                                    location=location,
                                    url=job_url,
                                    description=description,
                                    source=self.name,
                                    job_type=jt,
                                    salary=salary if salary else None,
                                    posted_date=posted_date,
                                )
                            )

                        if len(jobs) >= self.max_results:
                            break

                        time.sleep(1)  # Be polite to the API

                    if len(jobs) >= self.max_results:
                        break
                if len(jobs) >= self.max_results:
                    break

        except Exception as e:
            print(f"[{self.name}] Error: {e}")

        return jobs
