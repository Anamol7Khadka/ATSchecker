"""
stepstone.py — StepStone Germany scraper using undetected-chromedriver.
Uses undetected-chromedriver to bypass Cloudflare bot detection,
and JavaScript-based DOM extraction for resilient scraping.
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


class StepStoneScraper(BaseScraper):
    name = "StepStone"
    BASE_URL = "https://www.stepstone.de/jobs"

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

    def _extract_jobs_via_js(self, driver) -> list:
        """Extract job data using JavaScript — more resilient than CSS selectors."""
        script = """
        var jobs = [];
        // Strategy 1: Look for links that point to job detail pages
        var links = document.querySelectorAll('a[href*="/stellenangebote--"], a[href*="/jobs/"], a[href*="offer"]');
        var seen = new Set();
        for (var i = 0; i < links.length; i++) {
            var a = links[i];
            var href = a.href || '';
            if (!href || seen.has(href)) continue;
            // Skip nav/filter links
            if (href.includes('/filter') || href.includes('/search') || href.includes('#')) continue;
            // Must look like a job detail page
            if (href.includes('stepstone.de') && (href.includes('stellenangebote') || href.includes('/offer'))) {
                seen.add(href);
                // Try to extract structured data from the card container
                var container = a.closest('article') || a.closest('[data-testid]') || a.closest('li') || a.parentElement;
                var title = '';
                var company = '';
                var location = '';

                // Title: prefer the link text or h2/h3 within
                var hEl = container ? (container.querySelector('h2') || container.querySelector('h3')) : null;
                title = hEl ? hEl.textContent.trim() : a.textContent.trim();

                // Company: look for specific patterns
                if (container) {
                    var spans = container.querySelectorAll('span');
                    for (var j = 0; j < spans.length; j++) {
                        var text = spans[j].textContent.trim();
                        if (text && text.length > 2 && text.length < 80 && text !== title && !text.includes('€')) {
                            if (!company) company = text;
                            else if (!location && (text.includes(',') || text.match(/\\d{5}/))) location = text;
                        }
                    }
                }

                if (title && title.length > 3 && title.length < 200) {
                    jobs.push({title: title, company: company, location: location, url: href});
                }
            }
        }

        // Strategy 2: Look for article elements if strategy 1 found nothing
        if (jobs.length === 0) {
            var articles = document.querySelectorAll('article');
            for (var k = 0; k < articles.length; k++) {
                var art = articles[k];
                var aLink = art.querySelector('a');
                var aTitle = art.querySelector('h2, h3');
                if (aLink && aTitle) {
                    var artUrl = aLink.href || '';
                    if (artUrl && !seen.has(artUrl)) {
                        seen.add(artUrl);
                        jobs.push({
                            title: aTitle.textContent.trim(),
                            company: '',
                            location: '',
                            url: artUrl
                        });
                    }
                }
            }
        }

        return jobs;
        """
        try:
            return driver.execute_script(script) or []
        except Exception:
            return []

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
            print(f"[{self.name}] No scraping backend available.")

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
                        f"{self.BASE_URL}"
                        f"?q={quote_plus(query)}"
                        f"&loc={quote_plus(city)}"
                        f"&radius=30&sort=2"
                    )

                    driver.get(url)
                    time.sleep(self.delay + 3)

                    # Handle cookie consent
                    try:
                        for btn_sel in [
                            "button[data-testid='uc-accept-all-button']",
                            "#ccmgt_explicit_accept",
                            "button.consent-accept",
                            "#onetrust-accept-btn-handler",
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

                    # Scroll to load lazy content
                    for _ in range(3):
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        time.sleep(1)

                    # Extract via JavaScript (resilient to React DOM changes)
                    raw_jobs = self._extract_jobs_via_js(driver)

                    for rj in raw_jobs[:self.max_results]:
                        title = rj.get("title", "").strip()
                        job_url = rj.get("url", "").strip()

                        if not title or not job_url:
                            continue
                        if any(j.url == job_url for j in jobs):
                            continue

                        jobs.append(
                            JobPosting(
                                title=title,
                                company=rj.get("company", "").strip(),
                                location=rj.get("location", "").strip() or city,
                                url=job_url,
                                source=self.name,
                                job_type=jt,
                                posted_date=datetime.now().isoformat(),
                            )
                        )

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

    def _scrape_ddg_fallback(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        """Search DuckDuckGo for StepStone job listings as a fallback."""
        import warnings
        jobs = []
        seen_urls = set()

        for jt in job_types[:6]:
            for kw in keywords[:6]:
                query = f"{jt} {kw} {city} Germany stepstone.de jobs"
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        with DDGS() as ddgs:
                            results = ddgs.text(query, max_results=15, region="de-de")
                    for r in results:
                        url = r.get("href", "")
                        if "stepstone" not in url.lower():
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
