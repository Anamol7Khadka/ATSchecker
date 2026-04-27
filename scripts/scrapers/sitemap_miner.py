"""
sitemap_miner.py — Passive job URL discovery from company sitemaps.

Fetches sitemap.xml from target company domains and extracts job-related URLs.
Zero bot detection risk — sitemaps are public, machine-readable files.
Catches jobs BEFORE search engines index them (sitemaps update within minutes).
"""

import re
import time
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests

from scrapers.base import BaseScraper, JobPosting

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


class SitemapMiner(BaseScraper):
    name = "SitemapMiner"

    DEFAULT_DOMAINS = [
        "bosch.com",
        "siemens.com",
        "sap.com",
        "bmwgroup.com",
        "volkswagen-group.com",
        "zalando.com",
        "infineon.com",
        "mercedes-benz.com",
        "continental.com",
        "deutschebahn.com",
        "celonis.com",
        "basf.com",
        "henkel.de",
        "thyssenkrupp.com",
    ]

    SITEMAP_PATHS = [
        "/sitemap.xml",
        "/sitemap_index.xml",
        "/career-sitemap.xml",
        "/jobs-sitemap.xml",
        "/karriere/sitemap.xml",
        "/en/sitemap.xml",
        "/de/sitemap.xml",
    ]

    JOB_PATH_PATTERNS = re.compile(
        r"(?:career|job|jobs|stellen|karriere|vacancy|position|opening|posting|"
        r"werkstudent|praktikum|thesis|stellenangebot)",
        re.IGNORECASE,
    )

    # Paths that are definitely NOT individual job postings
    EXCLUDE_PATTERNS = re.compile(
        r"(?:/tag/|/category/|/page/\d|/feed|\.css|\.js|\.png|\.jpg|/wp-content/|"
        r"/search|/filter|/login|/register|/impressum|/datenschutz|/privacy)",
        re.IGNORECASE,
    )

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        jobs = []
        seen_urls = set()
        domains = self._get_domains()

        for domain in domains:
            domain_jobs = self._mine_domain(domain, city, keywords, job_types, seen_urls)
            jobs.extend(domain_jobs)

            if len(jobs) >= self.max_results:
                break

            time.sleep(0.2)  # Brief pause between domains

        self.log(f"[{self.name}] Total: {len(jobs)} job URLs from {len(domains)} sitemaps for {city}")
        return jobs

    def _mine_domain(
        self, domain: str, city: str, keywords: List[str],
        job_types: List[str], seen_urls: set
    ) -> List[JobPosting]:
        jobs = []

        for sitemap_path in self.SITEMAP_PATHS:
            urls_to_check = []
            for scheme_prefix in ["https://www.", "https://", "https://careers."]:
                sitemap_url = f"{scheme_prefix}{domain}{sitemap_path}"
                try:
                    resp = requests.get(sitemap_url, timeout=10, headers={
                        "User-Agent": "Mozilla/5.0 (compatible; ATSchecker/2.0)",
                    })
                    if resp.status_code != 200:
                        continue

                    root = ElementTree.fromstring(resp.content)

                    # Handle sitemap index (contains links to other sitemaps)
                    sitemap_refs = root.findall(f"{SITEMAP_NS}sitemap")
                    if sitemap_refs:
                        for sm_ref in sitemap_refs:
                            loc_el = sm_ref.find(f"{SITEMAP_NS}loc")
                            if loc_el is not None and loc_el.text:
                                sm_url = loc_el.text.strip()
                                if self.JOB_PATH_PATTERNS.search(sm_url):
                                    # This sub-sitemap likely contains job URLs
                                    sub_urls = self._parse_sitemap(sm_url)
                                    urls_to_check.extend(sub_urls)
                        break  # Found a working sitemap index

                    # Direct URL list
                    url_elements = root.findall(f"{SITEMAP_NS}url")
                    for url_el in url_elements:
                        loc_el = url_el.find(f"{SITEMAP_NS}loc")
                        lastmod_el = url_el.find(f"{SITEMAP_NS}lastmod")
                        if loc_el is not None and loc_el.text:
                            page_url = loc_el.text.strip()
                            lastmod = lastmod_el.text.strip() if lastmod_el is not None and lastmod_el.text else ""
                            urls_to_check.append((page_url, lastmod))

                    if urls_to_check:
                        break  # Found a working sitemap

                except ElementTree.ParseError:
                    continue
                except Exception:
                    continue

            # Filter URLs for job-related ones
            company = self._company_from_domain(domain)
            for item in urls_to_check:
                if isinstance(item, tuple):
                    page_url, lastmod = item
                else:
                    page_url, lastmod = item, ""

                if not page_url or page_url in seen_urls:
                    continue
                if not self.JOB_PATH_PATTERNS.search(page_url):
                    continue
                if self.EXCLUDE_PATTERNS.search(page_url):
                    continue

                seen_urls.add(page_url)

                # Extract a rough title from the URL path
                title = self._title_from_url(page_url)
                if not title or len(title) < 5:
                    continue

                jobs.append(
                    JobPosting(
                        title=title,
                        company=company,
                        location=city,
                        url=page_url,
                        description=f"Discovered via sitemap of {domain}",
                        source=self.name,
                        posted_date=lastmod,
                    )
                )

                if len(jobs) >= self.max_results:
                    break

            if urls_to_check:
                break  # Don't try more sitemap paths if we found one

        return jobs

    def _parse_sitemap(self, sitemap_url: str) -> list:
        """Parse a sub-sitemap and return (url, lastmod) tuples."""
        results = []
        try:
            resp = requests.get(sitemap_url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (compatible; ATSchecker/2.0)",
            })
            if resp.status_code != 200:
                return results
            root = ElementTree.fromstring(resp.content)
            for url_el in root.findall(f"{SITEMAP_NS}url"):
                loc_el = url_el.find(f"{SITEMAP_NS}loc")
                lastmod_el = url_el.find(f"{SITEMAP_NS}lastmod")
                if loc_el is not None and loc_el.text:
                    lastmod = lastmod_el.text.strip() if lastmod_el is not None and lastmod_el.text else ""
                    results.append((loc_el.text.strip(), lastmod))
        except Exception:
            pass
        return results

    def _get_domains(self) -> List[str]:
        domains = list(self.DEFAULT_DOMAINS)
        config_domains = self.config.get("company_domains", [])
        if isinstance(config_domains, list):
            domains.extend(config_domains)

        # Add profile-specific career page domains
        hints = self.config.get("scraper_hints", {})
        if isinstance(hints, dict):
            for cp in hints.get("career_pages", []):
                if isinstance(cp, dict) and "url" in cp:
                    parsed = urlparse(cp["url"])
                    if parsed.netloc:
                        domain = parsed.netloc.replace("www.", "")
                        domains.append(domain)

        return list(dict.fromkeys(domains))

    @staticmethod
    def _company_from_domain(domain: str) -> str:
        root = domain.split(".")[0]
        return root.replace("-", " ").title()

    @staticmethod
    def _title_from_url(url: str) -> str:
        """Extract a human-readable title from a URL path."""
        try:
            from urllib.parse import unquote
            path = unquote(urlparse(url).path)
            segments = [s for s in path.split("/") if s and len(s) > 3]
            if segments:
                # Use the longest segment (usually the job title slug)
                title_segment = max(segments, key=len)
                title = title_segment.replace("-", " ").replace("_", " ").strip()
                title = " ".join(w.capitalize() for w in title.split())
                return title[:150]
        except Exception:
            pass
        return ""
