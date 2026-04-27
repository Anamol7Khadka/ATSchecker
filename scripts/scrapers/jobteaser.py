"""
jobteaser.py — Jobteaser scraper using Selenium.
Jobteaser is a university-connected job board popular in Europe.
Falls back to DuckDuckGo search if SSO/login is required.
"""

import time
from datetime import datetime
from typing import List
from urllib.parse import quote_plus

from scrapers.base import BaseScraper, JobPosting, is_listing_page

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    UC_AVAILABLE = True
except ImportError:
    UC_AVAILABLE = False

try:
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDG_AVAILABLE = True
    except ImportError:
        DDG_AVAILABLE = False

def _detect_chrome_version():
    import subprocess, re as _re, platform
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon")
            ver, _ = winreg.QueryValueEx(key, "version")
            m = _re.match(r"(\d+)", ver)
            if m: return int(m.group(1))
        except Exception: pass
    for path in ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "google-chrome", "chromium-browser", "chromium"]:
        try:
            out = subprocess.check_output([path, "--version"], stderr=subprocess.DEVNULL, text=True)
            m = _re.search(r"(\d+)", out)
            if m: return int(m.group(1))
        except Exception: continue
    return None

_CHROME_VERSION = _detect_chrome_version()


class JobteaserScraper(BaseScraper):
    name = "Jobteaser"

    def _build_query_variants(self, city: str, job_type: str, keyword: str) -> List[str]:
        base = [f"{job_type} {keyword}"]
        city_l = city.lower()

        # OVGU/campus boost for Magdeburg and university-focused discovery.
        if city_l == "magdeburg" or self.config.get("enable_university_discovery", True):
            base.extend(
                [
                    f"{job_type} {keyword} OVGU",
                    f"{job_type} {keyword} Otto von Guericke",
                    f"{job_type} {keyword} university campus",
                ]
            )

        if "thesis" in job_type.lower() or "master" in job_type.lower() or "arbeit" in job_type.lower():
            base.extend(
                [
                    f"{job_type} {keyword} research",
                    f"{job_type} {keyword} lab",
                ]
            )

        # Keep order but remove duplicates.
        dedup = []
        seen = set()
        for q in base:
            ql = q.lower()
            if ql in seen:
                continue
            seen.add(ql)
            dedup.append(q)
        return dedup[:5]

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
        jobs = []

        # Jobteaser typically requires SSO — try direct first, then DDG fallback
        if UC_AVAILABLE:
            jobs = self._scrape_selenium(city, keywords, job_types)

        if len(jobs) < 3 and DDG_AVAILABLE:
            ddg_jobs = self._scrape_ddg_fallback(city, keywords, job_types)
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
                    for query in self._build_query_variants(city, jt, kw):
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
                                print(f"[{self.name}] SSO/login required, using DuckDuckGo fallback.")
                                break

                        # Scroll to load content
                        for _ in range(2):
                            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                            time.sleep(1)

                        # Find job cards
                        card_selectors = [
                            "article",
                            "div[class*='job-offer']",
                            "li[class*='job']",
                            "a[class*='offer']",
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

                                source = self.name
                                if "ovgu" in query.lower() or city.lower() == "magdeburg":
                                    source = "Jobteaser→OVGU"

                                jobs.append(
                                    JobPosting(
                                        title=title,
                                        company="",
                                        location=city,
                                        url=job_url or url,
                                        source=source,
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
        """Fallback: use DuckDuckGo search to find Jobteaser listings."""
        jobs = []

        for jt in job_types[:6]:
            for kw in keywords[:6]:
                # DDG doesn't reliably enforce site:, so include domain + OVGU terms.
                query = f"{jt} {kw} {city} Germany jobteaser.com ovgu thesis campus"

                try:
                    with DDGS() as ddgs:
                        results = list(ddgs.text(query, max_results=10, region="de-de"))
                except Exception as e:
                    print(f"[{self.name}] DuckDuckGo fallback failed: {e}")
                    time.sleep(2)
                    continue

                for r in results:
                    url = r.get("href", "") or r.get("link", "")
                    title = r.get("title", "")

                    if not url or not isinstance(url, str):
                        continue
                    if "jobteaser.com" not in url.lower():
                        continue
                    # Skip search/listing pages
                    if is_listing_page(title, url):
                        continue
                    if any(j.url == url for j in jobs):
                        continue

                    jobs.append(
                        JobPosting(
                            title=title if title else f"{jt} — {kw}",
                            company="(via Jobteaser/DDG)",
                            location=city,
                            url=url,
                            source="DDG→Jobteaser/OVGU",
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
