"""
linkedin.py — LinkedIn public job search scraper using Selenium.
Scrapes LinkedIn's public job search pages (no login required for first ~25 results).
"""

import time
from typing import List

from scrapers.base import BaseScraper, JobPosting

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


class LinkedInScraper(BaseScraper):
    name = "LinkedIn"

    BASE_URL = "https://www.linkedin.com/jobs/search/"

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
            #  Fallback: try system Chrome
            return webdriver.Chrome(options=opts)

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        if not SELENIUM_AVAILABLE:
            print(f"[{self.name}] Selenium not installed, skipping.")
            return []

        jobs = []
        driver = None

        try:
            driver = self._get_driver()

            for jt in job_types[:6]:
                for kw in keywords[:6]:
                    query = f"{jt} {kw}"
                    location = f"{city}, Germany"

                    url = (
                        f"{self.BASE_URL}?keywords={query.replace(' ', '%20')}"
                        f"&location={location.replace(' ', '%20').replace(',', '%2C')}"
                    )

                    driver.get(url)
                    time.sleep(self.delay + 1)

                    # Scroll to load more results
                    for _ in range(12):
                        driver.execute_script(
                            "window.scrollTo(0, document.body.scrollHeight);"
                        )
                        time.sleep(1)

                    # Try to find job cards
                    cards = []
                    selectors = [
                        "div.base-card",
                        "li.jobs-search-results__list-item",
                        "div.job-search-card",
                        "ul.jobs-search__results-list li",
                    ]

                    for sel in selectors:
                        try:
                            cards = driver.find_elements(By.CSS_SELECTOR, sel)
                            if cards:
                                break
                        except Exception:
                            continue

                    for card in cards[: self.max_results]:
                        try:
                            # Title
                            title_el = None
                            for t_sel in [
                                "h3.base-search-card__title",
                                "a.base-card__full-link",
                                "h3",
                                ".job-search-card__title",
                            ]:
                                try:
                                    title_el = card.find_element(By.CSS_SELECTOR, t_sel)
                                    if title_el:
                                        break
                                except Exception:
                                    continue

                            title = title_el.text.strip() if title_el else f"{jt} - {kw}"

                            # Company
                            company = ""
                            for c_sel in [
                                "h4.base-search-card__subtitle",
                                "a.hidden-nested-link",
                                ".job-search-card__company-name",
                            ]:
                                try:
                                    comp_el = card.find_element(By.CSS_SELECTOR, c_sel)
                                    company = comp_el.text.strip()
                                    if company:
                                        break
                                except Exception:
                                    continue

                            # Location
                            loc = city
                            for l_sel in [
                                "span.job-search-card__location",
                                ".base-search-card__metadata",
                            ]:
                                try:
                                    loc_el = card.find_element(By.CSS_SELECTOR, l_sel)
                                    loc = loc_el.text.strip() or city
                                    if loc:
                                        break
                                except Exception:
                                    continue

                            # Posted date
                            posted_date = ""
                            for d_sel in ["time", "span.job-search-card__listdate", "span.job-search-card__listdate--new"]:
                                try:
                                    d_el = card.find_element(By.CSS_SELECTOR, d_sel)
                                    if d_el and d_el.text.strip():
                                        posted_date = d_el.text.strip()
                                        break
                                except Exception:
                                    continue

                            # URL
                            job_url = ""
                            try:
                                link_el = card.find_element(By.CSS_SELECTOR, "a")
                                job_url = link_el.get_attribute("href") or ""
                            except Exception:
                                pass

                            if not job_url:
                                continue

                            # Deduplicate
                            if any(j.url == job_url for j in jobs):
                                continue

                            jobs.append(
                                JobPosting(
                                    title=title,
                                    company=company,
                                    location=loc,
                                    url=job_url,
                                    description="",
                                    source=self.name,
                                    job_type=jt,
                                    posted_date=posted_date,
                                )
                            )

                        except Exception:
                            continue

                    if len(jobs) >= self.max_results:
                        break
                if len(jobs) >= self.max_results:
                    break

        except Exception as e:
            print(f"[{self.name}] Error scraping {city}: {e}")
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        return jobs
