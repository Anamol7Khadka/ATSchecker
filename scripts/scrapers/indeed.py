"""
indeed.py — Indeed Germany scraper using requests + BeautifulSoup.
"""

import time
import re
from datetime import datetime, timedelta
from typing import List

import requests
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, JobPosting


class IndeedScraper(BaseScraper):
    name = "Indeed"
    BASE_URL = "https://de.indeed.com/jobs"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
    }

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        jobs = []

        try:
            for jt in job_types:
                for kw in keywords:
                    query = f"{jt} {kw}"

                    params = {
                        "q": query,
                        "l": f"{city}",
                        "sort": "date",
                    }

                    try:
                        resp = requests.get(
                            self.BASE_URL,
                            params=params,
                            headers=self.HEADERS,
                            timeout=15,
                        )
                        resp.raise_for_status()
                    except Exception as e:
                        print(f"[{self.name}] Request failed for '{query}' in {city}: {e}")
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")

                    # Try multiple card selectors (Indeed changes layout frequently)
                    card_selectors = [
                        "div.job_seen_beacon",
                        "div.jobsearch-ResultsList div.result",
                        "td.resultContent",
                        "div[data-jk]",
                        "li div.cardOutline",
                    ]

                    cards = []
                    for sel in card_selectors:
                        cards = soup.select(sel)
                        if cards:
                            break

                    for card in cards[: self.max_results]:
                        try:
                            # Title
                            title_el = card.select_one(
                                "h2.jobTitle a, a[data-jk], h2.jobTitle span, .jobTitle"
                            )
                            title = title_el.get_text(strip=True) if title_el else ""

                            # Company
                            comp_el = card.select_one(
                                "span.companyName, span[data-testid='company-name'], .company"
                            )
                            company = comp_el.get_text(strip=True) if comp_el else ""

                            # Location
                            loc_el = card.select_one(
                                "div.companyLocation, span.companyLocation, .location"
                            )
                            loc = loc_el.get_text(strip=True) if loc_el else city

                            # Posted date
                            date_text = ""
                            for d_sel in ["span.date", "span[data-testid='myjobs-serp-date']", "span.result-date", "span.date" ]:
                                try:
                                    d_el = card.select_one(d_sel)
                                    if d_el and d_el.get_text(strip=True):
                                        date_text = d_el.get_text(strip=True)
                                        break
                                except Exception:
                                    continue

                            posted_date = self._parse_date(date_text)

                            # URL
                            link_el = card.select_one("a[href*='/rc/clk'], a[data-jk], h2.jobTitle a")
                            job_url = ""
                            if link_el:
                                href = link_el.get("href", "")
                                if href.startswith("/"):
                                    job_url = f"https://de.indeed.com{href}"
                                elif href.startswith("http"):
                                    job_url = href

                            # Snippet
                            snippet_el = card.select_one(
                                "div.job-snippet, td.snip, .job-snippet"
                            )
                            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                            if not title:
                                continue

                            # Deduplicate
                            if job_url and any(j.url == job_url for j in jobs):
                                continue

                            jobs.append(
                                JobPosting(
                                    title=title,
                                    company=company,
                                    location=loc,
                                    url=job_url or f"https://de.indeed.com/jobs?q={query.replace(' ', '+')}",
                                    description=snippet[:1000],
                                    source=self.name,
                                    job_type=jt,
                                        posted_date=posted_date,
                                )
                            )

                        except Exception:
                            continue

                    time.sleep(self.delay)

                    if len(jobs) >= self.max_results:
                        break
                if len(jobs) >= self.max_results:
                    break

        except Exception as e:
            print(f"[{self.name}] Error: {e}")

        return jobs

    @staticmethod
    def _parse_date(text: str) -> str:
        """Convert relative/short date text to ISO date string."""
        if not text:
            return ""
        t = text.strip().lower()
        today = datetime.now()

        if any(k in t for k in ["heute", "today", "just posted", "vor wenigen minuten", "minutes ago", "minuten"]):
            return today.isoformat()

        match = re.search(r"vor\s+(\d+)\s*tag", t) or re.search(r"(\d+)\s+days?", t)
        if match:
            days = int(match.group(1))
            return (today - timedelta(days=days)).isoformat()

        match_h = re.search(r"(\d+)\s*hour", t) or re.search(r"vor\s+(\d+)\s*stunde", t)
        if match_h:
            hours = int(match_h.group(1))
            return (today - timedelta(hours=hours)).isoformat()

        return ""
