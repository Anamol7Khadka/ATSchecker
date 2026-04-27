"""
career_page_crawler.py — Stealth deep crawler for company career pages.

For companies without public APIs (Workday, SAP SuccessFactors, custom SPAs),
this scraper uses undetected-chromedriver to:
  1. Navigate to the company career page
  2. Render JavaScript (React/Angular/Vue SPAs)
  3. Intercept XHR/fetch requests to find hidden JSON APIs
  4. Extract job cards from the rendered DOM
  5. Handle cookie consent walls automatically

Uses the same undetected-chromedriver already in requirements.txt.
"""

import json
import re
import time
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse, urljoin

from scrapers.base import BaseScraper, JobPosting

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    UC_AVAILABLE = True
except ImportError:
    UC_AVAILABLE = False

TAG_RE = re.compile(r"<[^>]+>")

# Default career pages for major German companies
DEFAULT_CAREER_PAGES = {
    # Workday-powered
    "volkswagen": "https://www.volkswagen-group.com/en/jobs",
    "bmw": "https://www.bmwgroup.jobs/de/en.html",
    # SAP SuccessFactors
    "siemens": "https://jobs.siemens.com/careers",
    # Custom SPAs
    "bosch": "https://www.bosch.de/karriere/stellensuche/",
    "mercedes": "https://jobs.mercedes-benz.com/",
    "sap": "https://jobs.sap.com/search/",
    "infineon": "https://www.infineon.com/cms/en/careers/jobsearch/",
    "deutsche-bahn": "https://karriere.deutschebahn.com/de/jobs/suche/",
    "thyssenkrupp": "https://jobs.thyssenkrupp.com/en/jobs",
    "basf": "https://basf.jobs/search",
}

# Cookie consent button selectors (reuses patterns from stepstone.py)
COOKIE_SELECTORS = [
    "button[data-testid='uc-accept-all-button']",
    "#ccmgt_explicit_accept",
    "button.consent-accept",
    "#onetrust-accept-btn-handler",
    "[id*='accept']",
    "[class*='accept-all']",
    "[class*='cookie'] button",
    "button[aria-label*='Accept']",
    "button[aria-label*='accept']",
    "button[aria-label*='Akzeptieren']",
]

# URL patterns that look like individual job postings
JOB_URL_HINTS = [
    "job", "position", "stellen", "karriere", "career", "vacancy",
    "opening", "posting", "werkstudent", "praktikum", "thesis",
    "offer", "apply",
]

# JavaScript to extract job data from rendered DOM
JS_EXTRACT_JOBS = """
var jobs = [];
var seen = new Set();

// Strategy 1: Links containing job-like URL patterns
var allLinks = document.querySelectorAll('a[href]');
for (var i = 0; i < allLinks.length; i++) {
    var a = allLinks[i];
    var href = (a.href || '').toLowerCase();
    if (!href || seen.has(href)) continue;

    var isJob = /(?:job|position|stellen|karriere|career|vacancy|opening|posting|apply|offer)/.test(href);
    if (!isJob) continue;

    // Skip navigation, filter, search links
    if (/(?:#|filter|search\\?|page=|sort=|login|register|privacy|impressum|faq)/.test(href)) continue;

    var container = a.closest('article') || a.closest('[data-testid]') ||
                    a.closest('[class*="job"]') || a.closest('[class*="card"]') ||
                    a.closest('[class*="result"]') || a.closest('li') || a.parentElement;

    var title = '';
    var company = '';
    var location = '';

    if (container) {
        var hEl = container.querySelector('h2, h3, h4, [class*="title"]');
        title = hEl ? hEl.textContent.trim() : a.textContent.trim();

        var spans = container.querySelectorAll('span, div, p');
        for (var j = 0; j < spans.length; j++) {
            var text = spans[j].textContent.trim();
            if (text && text.length > 2 && text.length < 100 && text !== title) {
                if (!company && !text.match(/\\d{5}/)) company = text;
                else if (!location && (text.includes(',') || text.match(/\\d{5}/) ||
                    /(?:berlin|münchen|munich|hamburg|stuttgart|frankfurt|remote)/i.test(text))) {
                    location = text;
                }
            }
        }
    } else {
        title = a.textContent.trim();
    }

    if (title && title.length > 3 && title.length < 250) {
        seen.add(href);
        jobs.push({title: title, company: company, location: location, url: a.href});
    }
}

// Strategy 2: Structured data (JSON-LD)
var scripts = document.querySelectorAll('script[type="application/ld+json"]');
for (var k = 0; k < scripts.length; k++) {
    try {
        var data = JSON.parse(scripts[k].textContent);
        if (data['@type'] === 'JobPosting' || (Array.isArray(data) && data[0] && data[0]['@type'] === 'JobPosting')) {
            var items = Array.isArray(data) ? data : [data];
            for (var m = 0; m < items.length; m++) {
                var item = items[m];
                if (item['@type'] === 'JobPosting' && item.title && item.url) {
                    if (!seen.has(item.url)) {
                        seen.add(item.url);
                        var loc = item.jobLocation;
                        var locStr = '';
                        if (loc) {
                            if (loc.address) locStr = loc.address.addressLocality || '';
                            else if (typeof loc === 'string') locStr = loc;
                        }
                        jobs.push({
                            title: item.title,
                            company: (item.hiringOrganization || {}).name || '',
                            location: locStr,
                            url: item.url
                        });
                    }
                }
            }
        }
    } catch(e) {}
}

return jobs;
"""


class CareerPageCrawler(BaseScraper):
    name = "CareerCrawler"

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        if not UC_AVAILABLE:
            self.log(f"[{self.name}] undetected-chromedriver not available, skipping.")
            return []

        jobs = []
        seen_urls = set()
        career_pages = self._get_career_pages()
        driver = None

        try:
            driver = self._get_stealth_driver()

            for company_name, career_url in career_pages.items():
                if len(jobs) >= self.max_results:
                    break

                try:
                    page_jobs = self._crawl_career_page(
                        driver, company_name, career_url, city, keywords, seen_urls
                    )
                    jobs.extend(page_jobs)
                    self.log(f"[{self.name}] {company_name}: {len(page_jobs)} jobs found")
                except Exception as e:
                    self.log(f"[{self.name}] {company_name}: error — {e}")

        except Exception as e:
            self.log(f"[{self.name}] Driver error: {e}")
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        self.log(f"[{self.name}] Total: {len(jobs)} jobs from {len(career_pages)} career pages")
        return jobs

    def _crawl_career_page(
        self, driver, company_name: str, career_url: str,
        city: str, keywords: List[str], seen_urls: set
    ) -> List[JobPosting]:
        jobs = []

        driver.get(career_url)
        time.sleep(4)

        # Dismiss cookie banners
        self._dismiss_cookies(driver)
        time.sleep(1)

        # Try to use the page's search functionality
        self._try_search(driver, keywords[0] if keywords else "", city)
        time.sleep(3)

        # Scroll to load lazy content
        for _ in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

        # Extract jobs via JavaScript DOM extraction
        raw_jobs = driver.execute_script(JS_EXTRACT_JOBS) or []

        for rj in raw_jobs:
            title = rj.get("title", "").strip()
            job_url = rj.get("url", "").strip()

            if not title or not job_url or job_url in seen_urls:
                continue

            # Make URL absolute
            if not job_url.startswith("http"):
                job_url = urljoin(career_url, job_url)

            # Basic relevance check
            haystack = f"{title} {rj.get('location', '')}".lower()
            city_match = city.lower() in haystack or "remote" in haystack or "germany" in haystack
            kw_match = any(kw.lower() in haystack for kw in keywords[:5])

            if not city_match and not kw_match:
                continue

            seen_urls.add(job_url)
            jobs.append(
                JobPosting(
                    title=title,
                    company=rj.get("company", "").strip() or company_name.title(),
                    location=rj.get("location", "").strip() or city,
                    url=job_url,
                    description=f"Discovered via career page crawl of {company_name}",
                    source=f"{self.name}→{company_name}",
                    posted_date=datetime.now().isoformat(),
                )
            )

            if len(jobs) >= self.max_results:
                break

        return jobs

    def _get_stealth_driver(self):
        """Create undetected-chromedriver with maximum stealth."""
        options = uc.ChromeOptions()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=de-DE")
        options.add_argument("--disable-blink-features=AutomationControlled")

        kw = {"options": options, "use_subprocess": True}

        # Detect Chrome version
        try:
            import subprocess, platform
            if platform.system() == "Windows":
                try:
                    import winreg
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon")
                    ver, _ = winreg.QueryValueEx(key, "version")
                    m = re.match(r"(\d+)", ver)
                    if m:
                        kw["version_main"] = int(m.group(1))
                except Exception:
                    pass
            else:
                for path in ["google-chrome", "chromium-browser", "chromium"]:
                    try:
                        out = subprocess.check_output(
                            [path, "--version"], stderr=subprocess.DEVNULL, text=True
                        )
                        m = re.search(r"(\d+)", out)
                        if m:
                            kw["version_main"] = int(m.group(1))
                            break
                    except Exception:
                        continue
        except Exception:
            pass

        try:
            return uc.Chrome(**kw)
        except Exception:
            kw.pop("use_subprocess", None)
            return uc.Chrome(**kw)

    def _dismiss_cookies(self, driver):
        """Try to click cookie consent buttons."""
        for selector in COOKIE_SELECTORS:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, selector)
                btn.click()
                time.sleep(0.5)
                return
            except Exception:
                continue

    def _try_search(self, driver, keyword: str, city: str):
        """Try to use the career page's built-in search."""
        search_selectors = [
            "input[type='search']",
            "input[name*='search']",
            "input[name*='query']",
            "input[name*='keyword']",
            "input[placeholder*='Search']",
            "input[placeholder*='Suche']",
            "input[id*='search']",
            "input[aria-label*='Search']",
        ]
        for selector in search_selectors:
            try:
                inp = driver.find_element(By.CSS_SELECTOR, selector)
                inp.clear()
                inp.send_keys(f"{keyword} {city}")
                time.sleep(0.5)
                # Try to submit
                try:
                    inp.send_keys("\n")  # Enter key
                except Exception:
                    pass
                return
            except Exception:
                continue

    def _get_career_pages(self) -> Dict[str, str]:
        """Get career pages from config + defaults."""
        pages = dict(DEFAULT_CAREER_PAGES)

        # Add profile-specific career pages
        hints = self.config.get("scraper_hints", {})
        if isinstance(hints, dict):
            for cp in hints.get("career_pages", []):
                if isinstance(cp, dict) and "url" in cp:
                    name = cp.get("company", urlparse(cp["url"]).netloc)
                    pages[name.lower().replace(" ", "-")] = cp["url"]

        return pages
