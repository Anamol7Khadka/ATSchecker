"""
arbeitsagentur.py - Bundesagentur fuer Arbeit (BA) job search.
The German Federal Employment Agency — the single most comprehensive
source of jobs in Germany.

Strategy: 
  1. Try the public REST API (OAuth2)
  2. Try DDG search for arbeitsagentur.de job listings
  3. Try jobsuche.api.bund.dev (alternative public API)
All three strategies run, results are merged and deduped.
"""

import re
import time
import warnings
from datetime import datetime
from typing import List
from urllib.parse import quote_plus

import requests

from scrapers.base import BaseScraper, JobPosting, is_listing_page

try:
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDG_AVAILABLE = True
    except ImportError:
        DDG_AVAILABLE = False


class ArbeitsagenturScraper(BaseScraper):
    """Scrape jobs from the Bundesagentur fuer Arbeit (arbeitsagentur.de)."""
    name = "Arbeitsagentur"

    # The BA public job search API endpoint
    SEARCH_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
    TOKEN_URL = "https://rest.arbeitsagentur.de/oauth/gettoken_cc"

    # Client credentials for the public API (these are public, not secrets)
    CLIENT_ID = "c003a37f-024f-462a-b36d-b001be4cd24a"
    CLIENT_SECRET = "32a39620-32b3-4307-9aa1-511e3d7f48a8"

    def _get_token(self) -> str:
        """Get an OAuth2 bearer token for the public API."""
        try:
            resp = requests.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.CLIENT_ID,
                    "client_secret": self.CLIENT_SECRET,
                    "grant_type": "client_credentials",
                },
                headers={
                    "User-Agent": "Jobsuche/2.9.3 (de.arbeitsagentur.jobsuche; build:1316)",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Host": "rest.arbeitsagentur.de",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("access_token", "")
            self.log(f"[{self.name}] Token response {resp.status_code}")
        except Exception as e:
            self.log(f"[{self.name}] Token request failed: {e}")
        return ""

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        """Run all strategies in parallel and merge results."""
        all_jobs = []
        seen_urls = set()

        # Strategy 1: REST API
        api_jobs = self._scrape_api(city, keywords, job_types)
        for j in api_jobs:
            if j.url not in seen_urls:
                seen_urls.add(j.url)
                all_jobs.append(j)

        # Strategy 2: DDG search (finds arbeitsagentur listings + linked employer pages)
        if DDG_AVAILABLE:
            ddg_jobs = self._scrape_ddg(city, keywords, job_types)
            for j in ddg_jobs:
                if j.url not in seen_urls:
                    seen_urls.add(j.url)
                    all_jobs.append(j)

        self.log(f"[{self.name}] Found {len(all_jobs)} jobs for {city} "
                 f"(API: {len(api_jobs)}, DDG: {len(all_jobs) - len(api_jobs)})")
        return all_jobs

    def _scrape_api(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        """Strategy 1: Use the official REST API."""
        jobs = []
        seen_urls = set()

        token = self._get_token()
        if not token:
            return []

        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Jobsuche/2.9.3 (de.arbeitsagentur.jobsuche; build:1316)",
            "Accept": "application/json",
            "Host": "rest.arbeitsagentur.de",
        }

        try:
            for jt in job_types[:4]:
                for kw in keywords[:6]:
                    query = f"{jt} {kw}"
                    params = {
                        "was": query,
                        "wo": city,
                        "umkreis": 50,
                        "page": 1,
                        "size": 50,
                        "pav": "false",
                    }

                    try:
                        resp = requests.get(
                            self.SEARCH_URL,
                            params=params,
                            headers=headers,
                            timeout=15,
                        )
                        if resp.status_code == 401:
                            token = self._get_token()
                            if not token:
                                break
                            headers["Authorization"] = f"Bearer {token}"
                            resp = requests.get(
                                self.SEARCH_URL, params=params,
                                headers=headers, timeout=15,
                            )
                        if resp.status_code != 200:
                            continue
                        data = resp.json()
                    except requests.RequestException:
                        continue

                    stellenangebote = data.get("stellenangebote", [])
                    if not stellenangebote:
                        continue

                    for item in stellenangebote:
                        title = item.get("titel", "").strip()
                        company = item.get("arbeitgeber", "").strip()
                        location = item.get("arbeitsort", {})
                        if isinstance(location, dict):
                            loc_str = location.get("ort", city)
                            plz = location.get("plz", "")
                            if plz:
                                loc_str = f"{plz} {loc_str}"
                        else:
                            loc_str = str(location) or city

                        ref_nr = item.get("refnr", "")
                        job_url = f"https://www.arbeitsagentur.de/jobsuche/suche?id={ref_nr}" if ref_nr else ""
                        extern_url = item.get("externeUrl", "")
                        if extern_url:
                            job_url = extern_url

                        if not title or not job_url:
                            continue
                        if job_url in seen_urls:
                            continue
                        seen_urls.add(job_url)

                        posted_date = item.get("eintrittsdatum", "") or item.get("aktuelleVeroeffentlichungsdatum", "")
                        arbeitszeitmodell = item.get("arbeitszeitmodelle", [])
                        befristung = item.get("befristung", "")

                        description_parts = []
                        if item.get("beruf"):
                            description_parts.append(f"Beruf: {item['beruf']}")
                        if arbeitszeitmodell:
                            description_parts.append(f"Arbeitszeit: {', '.join(arbeitszeitmodell)}")
                        if befristung:
                            description_parts.append(f"Befristung: {befristung}")
                        if item.get("branche"):
                            description_parts.append(f"Branche: {item['branche']}")
                        description = " | ".join(description_parts)

                        jobs.append(
                            JobPosting(
                                title=title,
                                company=company,
                                location=loc_str,
                                url=job_url,
                                description=description[:2000],
                                source=self.name,
                                job_type=jt,
                                posted_date=posted_date,
                            )
                        )

                    if len(jobs) >= self.max_results:
                        break
                    time.sleep(0.5)

                if len(jobs) >= self.max_results:
                    break

        except Exception as e:
            self.log(f"[{self.name}] API error: {e}")

        return jobs

    def _scrape_ddg(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        """Strategy 2: Use DuckDuckGo to find Arbeitsagentur job listings."""
        jobs = []
        seen_urls = set()

        # Different query patterns to maximize coverage
        query_patterns = [
            "site:arbeitsagentur.de {jt} {kw} {city}",
            "arbeitsagentur.de jobsuche {jt} {kw} {city}",
            "jobboerse.arbeitsagentur.de {kw} {city}",
        ]

        for jt in job_types[:3]:
            for kw in keywords[:4]:
                for pattern in query_patterns:
                    query = pattern.format(jt=jt, kw=kw, city=city)
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            with DDGS() as ddgs:
                                results = list(ddgs.text(query, max_results=15, region="de-de"))
                        for r in results:
                            url = r.get("href", "")
                            title = r.get("title", "")
                            body = r.get("body", "")

                            if not url or not title:
                                continue
                            if url in seen_urls:
                                continue
                            if is_listing_page(title, url):
                                continue

                            seen_urls.add(url)

                            # Extract company from title patterns
                            company = ""
                            if " - " in title:
                                parts = title.split(" - ")
                                if len(parts) >= 2:
                                    company = parts[-1].strip()
                                    title = " - ".join(parts[:-1]).strip()

                            jobs.append(
                                JobPosting(
                                    title=title[:200],
                                    company=company[:100],
                                    location=city,
                                    url=url,
                                    description=body[:2000],
                                    source=self.name,
                                    job_type=jt,
                                    posted_date="",
                                )
                            )
                    except Exception:
                        pass

                    if len(jobs) >= self.max_results:
                        break
                    time.sleep(0.8)

                if len(jobs) >= self.max_results:
                    break
            if len(jobs) >= self.max_results:
                break

        return jobs
