"""
smartrecruiters_api.py — Direct JSON scraper for SmartRecruiters ATS.

Extracts highly structured job data bypassing all frontend scraping logic.
"""

import re
from datetime import datetime
from typing import List
from urllib.parse import urljoin

import requests

from scrapers.base import BaseScraper, JobPosting


class SmartRecruitersAPIScraper(BaseScraper):
    """Scrapes SmartRecruiters job boards using their public undocumented JSON API."""
    
    name = "SmartRecruitersAPI"

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        jobs = []
        
        # Pull specific companies to target from profile hints, or use defaults
        hints = self.config.get("scraper_hints", {})
        companies = ["bosch", "ubisoft", "visa", "square", "twitter", "linkedin"]
        
        if isinstance(hints, dict) and "ats_targets" in hints:
            ats_targets = hints["ats_targets"].get("smartrecruiters", [])
            if ats_targets:
                companies = ats_targets

        for company in companies:
            if len(jobs) >= self.max_results:
                break
                
            try:
                company_jobs = self._scrape_company(company, city, keywords)
                jobs.extend(company_jobs)
                self.log(f"[{self.name}] {company}: Found {len(company_jobs)} jobs")
            except Exception as e:
                self.log(f"[{self.name}] {company} error: {e}")

        return jobs

    def _scrape_company(self, company: str, city: str, keywords: List[str]) -> List[JobPosting]:
        jobs = []
        
        # SmartRecruiters public API for job postings
        api_url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
        
        try:
            resp = requests.get(api_url, timeout=10)
            if resp.status_code != 200:
                return []
                
            data = resp.json()
            postings = data.get("content", [])
            
            for post in postings:
                title = post.get("name", "")
                location_dict = post.get("location", {})
                location_str = f"{location_dict.get('city', '')}, {location_dict.get('country', '')}".strip(" ,")
                url = post.get("ref", "")
                
                if not url:
                    continue

                # Basic filtering
                haystack = f"{title} {location_str}".lower()
                city_match = city.lower() in haystack or "remote" in haystack or "germany" in haystack
                kw_match = any(kw.lower() in haystack for kw in keywords[:5]) if keywords else True

                if not city_match and not kw_match:
                    continue
                
                # Fetch full description if it passes basic filters
                desc = self._fetch_description(company, post.get("id"))
                
                jobs.append(JobPosting(
                    title=title,
                    company=company.title(),
                    location=location_str or city,
                    description=desc,
                    url=url,
                    source=self.name,
                    posted_date=post.get("releasedDate", datetime.now().isoformat()),
                ))
                
        except Exception as e:
            self.log(f"[{self.name}] Error scraping {company}: {e}")
            
        return jobs

    def _fetch_description(self, company: str, job_id: str) -> str:
        if not job_id:
            return ""
            
        api_url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{job_id}"
        try:
            resp = requests.get(api_url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                ad = data.get("jobAd", {})
                sections = ad.get("sections", {})
                
                desc_parts = []
                for section_name in ["companyDescription", "jobDescription", "qualifications", "additionalInformation"]:
                    if section := sections.get(section_name):
                        desc_parts.append(section.get("text", ""))
                        
                return "\n".join(desc_parts)
        except Exception:
            pass
            
        return ""
