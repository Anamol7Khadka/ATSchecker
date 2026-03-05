"""
stepstone.py — StepStone Germany scraper using Selenium.
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


class StepStoneScraper(BaseScraper):
    name = "StepStone"
    BASE_URL = "https://www.stepstone.de/jobs"

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
        if not SELENIUM_AVAILABLE:
            print(f"[{self.name}] Selenium not installed, skipping.")
            return []

        jobs = []
        driver = None

        try:
            driver = self._get_driver()

            for jt in job_types:
                for kw in keywords:
                    query = f"{jt} {kw}"
                    url = (
                        f"{self.BASE_URL}/{quote_plus(query)}"
                        f"/in-{quote_plus(city)}"
                        f"?radius=30"
                    )

                    driver.get(url)
                    time.sleep(self.delay + 2)

                    # Handle cookie consent
                    try:
                        cookie_btn = driver.find_element(
                            By.CSS_SELECTOR,
                            "button[data-testid='uc-accept-all-button'], "
                            "#ccmgt_explicit_accept, "
                            "button.consent-accept"
                        )
                        cookie_btn.click()
                        time.sleep(1)
                    except Exception:
                        pass

                    # Find job cards
                    card_selectors = [
                        "article[data-testid='job-item']",
                        "article.res-1p0dv5i",
                        "div[data-genesis-element='CARD']",
                        "a[data-testid='job-item-link']",
                        "article",
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
                            # Title
                            title = ""
                            for t_sel in [
                                "h2", "h3",
                                "[data-testid='job-item-title']",
                                "a span",
                            ]:
                                try:
                                    el = card.find_element(By.CSS_SELECTOR, t_sel)
                                    title = el.text.strip()
                                    if title:
                                        break
                                except Exception:
                                    continue

                            # Company
                            company = ""
                            for c_sel in [
                                "[data-testid='job-item-company']",
                                "span.res-btchsq",
                                "div span:nth-child(2)",
                            ]:
                                try:
                                    el = card.find_element(By.CSS_SELECTOR, c_sel)
                                    company = el.text.strip()
                                    if company:
                                        break
                                except Exception:
                                    continue

                            # Location
                            loc = city
                            for l_sel in [
                                "[data-testid='job-item-location']",
                                "span.res-1rsypix",
                            ]:
                                try:
                                    el = card.find_element(By.CSS_SELECTOR, l_sel)
                                    loc = el.text.strip() or city
                                    if loc:
                                        break
                                except Exception:
                                    continue

                            # URL
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
                                    company=company,
                                    location=loc,
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
            print(f"[{self.name}] Error scraping {city}: {e}")
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        return jobs
