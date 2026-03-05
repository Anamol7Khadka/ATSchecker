"""
xing.py — XING jobs scraper using Selenium.
Falls back to Google search + site:xing.com if login wall is encountered.
"""

import time
from datetime import datetime
from typing import List
from urllib.parse import quote_plus

from scrapers.base import BaseScraper, JobPosting

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from webdriver_manager.chrome import ChromeDriverManager

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

try:
    from googlesearch import search as google_search
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False


class XingScraper(BaseScraper):
    name = "XING"

    def _get_driver(self):
        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument(
            "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        try:
            service = Service(ChromeDriverManager().install())
            return webdriver.Chrome(service=service, options=opts)
        except Exception:
            return webdriver.Chrome(options=opts)

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        # Try Selenium first, fall back to Google search
        jobs = self._scrape_selenium(city, keywords, job_types)

        if not jobs and GOOGLE_AVAILABLE:
            jobs = self._scrape_google_fallback(city, keywords, job_types)

        return jobs

    def _scrape_selenium(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        if not SELENIUM_AVAILABLE:
            return []

        jobs = []
        driver = None

        try:
            driver = self._get_driver()

            for jt in job_types:
                for kw in keywords:
                    query = f"{jt} {kw}"
                    url = (
                        f"https://www.xing.com/jobs/search"
                        f"?keywords={quote_plus(query)}"
                        f"&location={quote_plus(city)}"
                    )

                    driver.get(url)
                    time.sleep(self.delay + 2)

                    # Check for login wall
                    page_source = driver.page_source.lower()
                    if "login" in page_source and "job" not in page_source[:500]:
                        print(f"[{self.name}] Login wall detected, falling back to Google.")
                        break

                    # Find job cards
                    card_selectors = [
                        "article",
                        "div[data-testid='search-result']",
                        "li.jobs-search-results-list-item",
                        "div.job-posting",
                    ]

                    cards = []
                    for sel in card_selectors:
                        try:
                            cards = driver.find_elements(By.CSS_SELECTOR, sel)
                            if cards:
                                break
                        except Exception:
                            continue

                    for card in cards[: self.max_results]:
                        try:
                            title = ""
                            for t_sel in ["h2", "h3", "a"]:
                                try:
                                    el = card.find_element(By.CSS_SELECTOR, t_sel)
                                    title = el.text.strip()
                                    if title:
                                        break
                                except Exception:
                                    continue

                            job_url = ""
                            try:
                                link = card.find_element(By.CSS_SELECTOR, "a")
                                job_url = link.get_attribute("href") or ""
                            except Exception:
                                pass

                            if not title or not job_url:
                                continue

                            if any(j.url == job_url for j in jobs):
                                continue

                            jobs.append(
                                JobPosting(
                                    title=title,
                                    company="",
                                    location=city,
                                    url=job_url,
                                    source=self.name,
                                    job_type=jt,
                                    posted_date=datetime.now().isoformat(),
                                )
                            )

                        except Exception:
                            continue

                    if len(jobs) >= self.max_results:
                        break
                if len(jobs) >= self.max_results:
                    break

        except Exception as e:
            print(f"[{self.name}] Selenium error: {e}")
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        return jobs

    def _scrape_google_fallback(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        """Fallback: use Google search with site:xing.com filter."""
        jobs = []

        for jt in job_types:
            for kw in keywords:
                query = f"{jt} {kw} {city} Germany site:xing.com/jobs"

                try:
                    results = list(google_search(query, num_results=5, lang="en"))
                except Exception as e:
                    print(f"[{self.name}] Google fallback failed: {e}")
                    continue

                for url in results:
                    if not isinstance(url, str) or "xing.com" not in url.lower():
                        continue

                    if any(j.url == url for j in jobs):
                        continue

                    jobs.append(
                        JobPosting(
                            title=f"{jt} — {kw}",
                            company="(via XING/Google)",
                            location=city,
                            url=url,
                            source=f"Google→XING",
                            job_type=jt,
                        )
                    )

                time.sleep(self.delay)

                if len(jobs) >= self.max_results:
                    break
            if len(jobs) >= self.max_results:
                break

        return jobs
