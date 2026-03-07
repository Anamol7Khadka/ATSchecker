"""
xing.py — XING jobs scraper using undetected-chromedriver.
Falls back to DuckDuckGo search + site:xing.com if login wall is encountered.
"""

import time
from datetime import datetime
from typing import List
from urllib.parse import quote_plus

from scrapers.base import BaseScraper, JobPosting

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    UC_AVAILABLE = True
except ImportError:
    UC_AVAILABLE = False

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

def _detect_chrome_version():
    import subprocess, re as _re
    for path in ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "google-chrome", "chromium-browser", "chromium"]:
        try:
            out = subprocess.check_output([path, "--version"], stderr=subprocess.DEVNULL, text=True)
            m = _re.search(r"(\d+)", out)
            if m: return int(m.group(1))
        except Exception: continue
    return None

_CHROME_VERSION = _detect_chrome_version()


class XingScraper(BaseScraper):
    name = "XING"

    def _get_driver(self):
        options = uc.ChromeOptions()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=de-DE")
        kw = {"options": options, "use_subprocess": True}
        if _CHROME_VERSION:
            kw["version_main"] = _CHROME_VERSION
        try:
            return uc.Chrome(**kw)
        except Exception:
            kw.pop("use_subprocess", None)
            return uc.Chrome(**kw)

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        # Try Selenium first, fall back to DuckDuckGo search
        jobs = []
        if UC_AVAILABLE:
            jobs = self._scrape_selenium(city, keywords, job_types)

        if len(jobs) < 5 and DDG_AVAILABLE:
            ddg_jobs = self._scrape_ddg_fallback(city, keywords, job_types)
            # Merge, avoiding URL duplicates
            existing_urls = {j.url for j in jobs}
            for j in ddg_jobs:
                if j.url not in existing_urls:
                    jobs.append(j)
                    existing_urls.add(j.url)

        return jobs

    def _scrape_selenium(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        jobs = []
        driver = None

        try:
            driver = self._get_driver()

            for jt in job_types[:6]:
                for kw in keywords[:6]:
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
                        print(f"[{self.name}] Login wall detected, falling back to DuckDuckGo.")
                        break

                    # Scroll to load content
                    for _ in range(3):
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        time.sleep(1)

                    # Find job cards
                    card_selectors = [
                        "article",
                        "div[data-testid='search-result']",
                        "li.jobs-search-results-list-item",
                        "div.job-posting",
                        "div[class*='JobCard']",
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

                            company = ""
                            try:
                                spans = card.find_elements(By.CSS_SELECTOR, "span, p")
                                for span in spans:
                                    text = span.text.strip()
                                    if text and text != title and len(text) > 2 and len(text) < 80:
                                        company = text
                                        break
                            except Exception:
                                pass

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

    def _scrape_ddg_fallback(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        """Fallback: use DuckDuckGo search to find XING job listings."""
        jobs = []

        for jt in job_types[:6]:
            for kw in keywords[:6]:
                # DDG doesn't support site: operator — use xing.com as keyword
                query = f"{jt} {kw} {city} Germany xing.com jobs"

                try:
                    with DDGS() as ddgs:
                        results = list(ddgs.text(query, max_results=20, region="de-de"))
                except Exception as e:
                    print(f"[{self.name}] DuckDuckGo fallback failed: {e}")
                    time.sleep(2)
                    continue

                for r in results:
                    url = r.get("href", "") or r.get("link", "")
                    title = r.get("title", "")

                    if not url or not isinstance(url, str):
                        continue
                    # Only keep XING URLs
                    if "xing.com" not in url.lower():
                        continue
                    if any(j.url == url for j in jobs):
                        continue

                    jobs.append(
                        JobPosting(
                            title=title if title else f"{jt} — {kw}",
                            company="(via XING/DDG)",
                            location=city,
                            url=url,
                            source="DDG→XING",
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
