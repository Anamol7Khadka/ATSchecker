"""
jobteaser.py — Jobteaser scraper using Selenium.
Jobteaser is a university-connected job board popular in Europe.
Falls back to Google search if SSO/login is required.
"""

import time
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


class JobteaserScraper(BaseScraper):
    name = "Jobteaser"

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
        # Jobteaser typically requires SSO — try direct first, then Google fallback
        jobs = []

        if SELENIUM_AVAILABLE:
            jobs = self._scrape_selenium(city, keywords, job_types)

        if not jobs and GOOGLE_AVAILABLE:
            jobs = self._scrape_google_fallback(city, keywords, job_types)

        return jobs

    def _scrape_selenium(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        jobs = []
        driver = None

        try:
            driver = self._get_driver()

            for jt in job_types:
                for kw in keywords:
                    query = f"{jt} {kw}"
                    url = (
                        f"https://www.jobteaser.com/en/job-offers"
                        f"?query={quote_plus(query)}"
                        f"&location={quote_plus(city + ', Germany')}"
                    )
                    driver.get(url)
                    time.sleep(self.delay + 2)

                    # Check for login/SSO wall
                    page_source = driver.page_source.lower()
                    if "sign in" in page_source or "log in" in page_source:
                        if "job" not in page_source[:1000]:
                            print(f"[{self.name}] SSO/login required, using Google fallback.")
                            break

                    # Find job cards
                    card_selectors = [
                        "article",
                        "div[class*='job-offer']",
                        "li[class*='job']",
                        "a[class*='offer']",
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
                            for t_sel in ["h2", "h3", "a", "span"]:
                                try:
                                    el = card.find_element(By.CSS_SELECTOR, t_sel)
                                    text = el.text.strip()
                                    if text and len(text) > 5:
                                        title = text
                                        break
                                except Exception:
                                    continue

                            job_url = ""
                            try:
                                link = card.find_element(By.CSS_SELECTOR, "a")
                                job_url = link.get_attribute("href") or ""
                            except Exception:
                                pass

                            if not title:
                                continue

                            if job_url and any(j.url == job_url for j in jobs):
                                continue

                            jobs.append(
                                JobPosting(
                                    title=title,
                                    company="",
                                    location=city,
                                    url=job_url or url,
                                    source=self.name,
                                    job_type=jt,
                                    posted_date=datetime.now().isoformat(),
                                )
                            )

                        except Exception:
                            continue

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
        jobs = []

        for jt in job_types:
            for kw in keywords:
                query = f"{jt} {kw} {city} Germany site:jobteaser.com"

                try:
                    results = list(google_search(query, num_results=5, lang="en"))
                except Exception as e:
                    print(f"[{self.name}] Google fallback failed: {e}")
                    continue

                for url in results:
                    if not isinstance(url, str) or "jobteaser.com" not in url.lower():
                        continue

                    if any(j.url == url for j in jobs):
                        continue

                    jobs.append(
                        JobPosting(
                            title=f"{jt} — {kw}",
                            company="(via Jobteaser/Google)",
                            location=city,
                            url=url,
                            source=f"Google→Jobteaser",
                            job_type=jt,
                            posted_date=datetime.now().isoformat(),
                        )
                    )

                time.sleep(self.delay)

                if len(jobs) >= self.max_results:
                    break
            if len(jobs) >= self.max_results:
                break

        return jobs
