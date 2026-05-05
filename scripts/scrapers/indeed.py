"""
indeed.py — Indeed Germany scraper using undetected-chromedriver + BeautifulSoup.
Uses undetected-chromedriver to bypass Indeed's anti-bot detection.
"""

import time
import re
import warnings
from datetime import datetime, timedelta
from typing import List

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, JobPosting, is_listing_page

try:
    import undetected_chromedriver as uc
    UC_AVAILABLE = True
except ImportError:
    UC_AVAILABLE = False

try:
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
    warnings.filterwarnings("ignore", message=r"This package .* renamed to `ddgs`.*")
    try:
        from duckduckgo_search import DDGS
        DDG_AVAILABLE = True
    except ImportError:
        DDG_AVAILABLE = False

def _detect_chrome_version():
    """Detect installed Chrome major version."""
    import subprocess, re as _re, platform
    # Windows: check registry
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon")
            ver, _ = winreg.QueryValueEx(key, "version")
            m = _re.match(r"(\d+)", ver)
            if m: return int(m.group(1))
        except Exception: pass
    # Mac/Linux
    for path in ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "google-chrome", "chromium-browser", "chromium"]:
        try:
            out = subprocess.check_output([path, "--version"], stderr=subprocess.DEVNULL, text=True)
            m = _re.search(r"(\d+)", out)
            if m: return int(m.group(1))
        except Exception: continue
    return None

_CHROME_VERSION = _detect_chrome_version()


class IndeedScraper(BaseScraper):
    name = "Indeed"
    BASE_URL = "https://de.indeed.com/jobs"

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

        # Try UC Selenium first
        if UC_AVAILABLE:
            jobs = self._scrape_selenium(city, keywords, job_types)

        # Fall back to DDG search if UC didn't find enough
        if len(jobs) < 5 and DDG_AVAILABLE:
            ddg_jobs = self._scrape_ddg_fallback(city, keywords, job_types)
            existing_urls = {j.url for j in jobs}
            for j in ddg_jobs:
                if j.url not in existing_urls:
                    jobs.append(j)
                    existing_urls.add(j.url)

        if not jobs and not UC_AVAILABLE and not DDG_AVAILABLE:
            print(f"[{self.name}] No scraping backend available (need undetected-chromedriver or duckduckgo_search).")

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
                    url = f"{self.BASE_URL}?q={query.replace(' ', '+')}&l={city}&sort=date"

                    try:
                        driver.get(url)
                        time.sleep(self.delay + 2)
                    except Exception as e:
                        print(f"[{self.name}] Navigation failed for '{query}' in {city}: {e}")
                        continue

                    # Handle cookie consent
                    try:
                        from selenium.webdriver.common.by import By
                        for btn_sel in [
                            "#onetrust-accept-btn-handler",
                            "button[id*='accept']",
                            "button[data-testid='uc-accept-all-button']",
                        ]:
                            try:
                                btn = driver.find_element(By.CSS_SELECTOR, btn_sel)
                                btn.click()
                                time.sleep(1)
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass

                    soup = BeautifulSoup(driver.page_source, "html.parser")

                    # Try multiple card selectors (Indeed changes layout frequently)
                    card_selectors = [
                        "div.job_seen_beacon",
                        "div.jobsearch-ResultsList div.result",
                        "td.resultContent",
                        "div[data-jk]",
                        "li div.cardOutline",
                        "div.tapItem",
                    ]

                    cards = []
                    for sel in card_selectors:
                        cards = soup.select(sel)
                        if cards:
                            break

                    for card in cards[: self.max_results]:
                        try:
                            title_el = card.select_one(
                                "h2.jobTitle a, a[data-jk], h2.jobTitle span, .jobTitle"
                            )
                            title = title_el.get_text(strip=True) if title_el else ""

                            comp_el = card.select_one(
                                "span.companyName, span[data-testid='company-name'], .company, [data-testid='company-name']"
                            )
                            company = comp_el.get_text(strip=True) if comp_el else ""

                            loc_el = card.select_one(
                                "div.companyLocation, span.companyLocation, .location"
                            )
                            loc = loc_el.get_text(strip=True) if loc_el else city

                            date_text = ""
                            for d_sel in ["span.date", "span[data-testid='myJobsStateDate']", "span.result-date"]:
                                d_el = card.select_one(d_sel)
                                if d_el and d_el.get_text(strip=True):
                                    date_text = d_el.get_text(strip=True)
                                    break

                            posted_date = self._parse_date(date_text)

                            link_el = card.select_one("a[href*='/rc/clk'], a[data-jk], h2.jobTitle a, a[href*='viewjob']")
                            job_url = ""
                            if link_el:
                                href = link_el.get("href", "")
                                if href.startswith("/"):
                                    job_url = f"https://de.indeed.com{href}"
                                elif href.startswith("http"):
                                    job_url = href

                            snippet_el = card.select_one(
                                "div.job-snippet, td.snip, .job-snippet"
                            )
                            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                            if not title:
                                continue

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
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        return jobs

    @staticmethod
    def _parse_date(text: str) -> str:
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

    def _scrape_ddg_fallback(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        """Search DuckDuckGo for Indeed job listings as a fallback."""
        import warnings
        jobs = []
        seen_urls = set()

        for jt in job_types[:6]:
            for kw in keywords[:6]:
                query = f"{jt} {kw} {city} Germany indeed.com jobs"
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        try:
                            with DDGS(timeout=30) as ddgs:
                                results = ddgs.text(query, max_results=15, region="de-de")
                        except TypeError:
                            # Fallback for older DDGS versions without timeout support
                            with DDGS() as ddgs:
                                results = ddgs.text(query, max_results=15, region="de-de")
                    for r in results:
                        url = r.get("href", "")
                        if "indeed" not in url.lower():
                            continue
                        if url in seen_urls:
                            continue
                        title = r.get("title", "")
                        # Skip search/listing pages
                        if is_listing_page(title, url):
                            continue
                        seen_urls.add(url)

                        title = r.get("title", "")
                        body = r.get("body", "")

                        # Try to extract company from title patterns like "Title - Company"
                        company = ""
                        if " - " in title:
                            parts = title.split(" - ")
                            if len(parts) >= 2:
                                company = parts[-1].strip()
                                title = " - ".join(parts[:-1]).strip()

                        jobs.append(
                            JobPosting(
                                title=title,
                                company=company,
                                location=city,
                                url=url,
                                description=body[:1000],
                                source=self.name,
                                job_type=jt,
                                posted_date="",
                            )
                        )
                except Exception:
                    continue

                if len(jobs) >= self.max_results:
                    break
            if len(jobs) >= self.max_results:
                break

        return jobs
