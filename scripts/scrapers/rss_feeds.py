"""
karriere_at.py - Karriere.at / Germany-wide job boards via RSS feeds.
Scrapes multiple free RSS/Atom job feeds that aggregate German job postings.
No API key needed, no anti-bot issues.
"""

import re
import time
from datetime import datetime
from typing import List
from xml.etree import ElementTree

import requests

from scrapers.base import BaseScraper, JobPosting


class RSSJobScraper(BaseScraper):
    """Scrape multiple free RSS/Atom job feeds for Germany."""
    name = "RSSFeeds"

    # Free RSS feeds with German tech jobs — no API key, no anti-bot
    RSS_FEEDS = {
        "Berlin Startup Jobs": "https://berlinstartupjobs.com/feed/",
        "GermanTechJobs": "https://germantechjobs.de/feeds/rss.xml",
        "HackerNews Jobs": "https://hnrss.org/whoishiring/new",
        "RemoteOK RSS": "https://remoteok.com/remote-jobs.rss",
        "WeAreDevelopers": "https://www.wearedevelopers.com/feed",
        "Arbeitnow RSS": "https://www.arbeitnow.com/api/job-board-api",
        # Engineering / IT specific
        "IT-Treff": "https://www.it-treff.de/rss.xml",
        "Ingenieur.de": "https://www.ingenieur.de/feed/", # Usually contains job posts
        "Heise Jobs": "https://www.heise.de/jobs/rss/topjobs.rdf",
    }

    # Additional targeted feeds built from search queries
    SEARCH_FEED_TEMPLATES = [
        # Indeed RSS (public, no auth)
        "https://de.indeed.com/rss?q={query}&l={city}&sort=date",
    ]

    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        jobs = []
        seen_urls = set()

        # 1. Scrape static RSS feeds
        for feed_name, feed_url in self.RSS_FEEDS.items():
            if feed_url.endswith("/api/job-board-api"):
                continue  # Skip Arbeitnow API (already have dedicated scraper)
            try:
                feed_jobs = self._parse_rss_feed(feed_url, feed_name, city, keywords)
                for job in feed_jobs:
                    if job.url not in seen_urls:
                        seen_urls.add(job.url)
                        jobs.append(job)
            except Exception as e:
                self.log(f"[{self.name}] {feed_name} error: {e}")
            time.sleep(0.5)

        # 2. Build targeted search feeds from profile
        for jt in job_types[:3]:
            for kw in keywords[:4]:
                query = f"{jt} {kw}"
                for tmpl in self.SEARCH_FEED_TEMPLATES:
                    try:
                        url = tmpl.format(
                            query=query.replace(" ", "+"),
                            city=city.replace(" ", "+"),
                        )
                        feed_jobs = self._parse_rss_feed(url, "Indeed RSS", city, keywords)
                        for job in feed_jobs:
                            if job.url not in seen_urls:
                                seen_urls.add(job.url)
                                jobs.append(job)
                    except Exception:
                        pass
                    time.sleep(0.5)

                if len(jobs) >= self.max_results:
                    break
            if len(jobs) >= self.max_results:
                break

        self.log(f"[{self.name}] Found {len(jobs)} jobs from RSS feeds for {city}")
        return jobs

    def _parse_rss_feed(
        self, url: str, source_name: str, default_city: str, keywords: List[str]
    ) -> List[JobPosting]:
        """Parse an RSS/Atom feed and extract job postings."""
        jobs = []
        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (compatible; ATSchecker/2.0)"
            })
            if resp.status_code != 200:
                return []

            root = ElementTree.fromstring(resp.content)

            # Handle RSS 2.0
            items = root.findall(".//item")
            if not items:
                # Handle Atom
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                items = root.findall(".//atom:entry", ns)

            for item in items[:self.max_results]:
                title = self._get_text(item, "title") or ""
                link = self._get_text(item, "link") or self._get_attr(item, "link", "href") or ""
                description = self._get_text(item, "description") or self._get_text(item, "summary") or ""
                pub_date = self._get_text(item, "pubDate") or self._get_text(item, "updated") or ""
                author = self._get_text(item, "author") or self._get_text(item, "dc:creator") or ""

                if not title or not link:
                    continue

                # Clean HTML from description
                description = self._clean_html(description)

                # Try to extract company from title pattern: "Title at Company" or "Title - Company"
                company = author
                if not company:
                    for sep in [" at ", " bei ", " - ", " | "]:
                        if sep in title:
                            parts = title.split(sep, 1)
                            if len(parts) == 2 and len(parts[1].strip()) > 2:
                                company = parts[1].strip()
                                title = parts[0].strip()
                                break

                # Extract location from description or title if possible
                location = default_city
                city_match = re.search(
                    r"(?:Berlin|Munich|Hamburg|Frankfurt|Stuttgart|Cologne|Dresden|Leipzig|"
                    r"Dusseldorf|Bonn|Hannover|Remote|Magdeburg|Braunschweig|Wolfsburg)",
                    f"{title} {description}", re.IGNORECASE
                )
                if city_match:
                    location = city_match.group(0)

                # Filter by keyword
                haystack = f"{title} {company} {description}".lower()
                if not any(kw.lower() in haystack for kw in keywords[:10]):
                    continue

                # Filter by location (unless remote)
                loc_lower = location.lower()
                city_lower = default_city.lower()
                if city_lower not in loc_lower and "remote" not in loc_lower and "germany" not in loc_lower and "deutschland" not in loc_lower:
                    continue

                # Parse date
                posted_date = self._parse_date(pub_date)

                jobs.append(
                    JobPosting(
                        title=title[:200],
                        company=company[:100],
                        location=location,
                        url=link,
                        description=description[:2000],
                        source=f"RSS/{source_name}",
                        posted_date=posted_date,
                    )
                )

        except ElementTree.ParseError:
            pass
        except Exception as e:
            self.log(f"[{self.name}] Error parsing {source_name}: {e}")

        return jobs

    @staticmethod
    def _get_text(element, tag: str) -> str:
        """Get text content of a child element."""
        # Try direct
        el = element.find(tag)
        if el is not None and el.text:
            return el.text.strip()
        # Try with Atom namespace
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        el = element.find(f"atom:{tag}", ns)
        if el is not None and el.text:
            return el.text.strip()
        return ""

    @staticmethod
    def _get_attr(element, tag: str, attr: str) -> str:
        """Get attribute of a child element."""
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for prefix in ["", "atom:"]:
            el = element.find(f"{prefix}{tag}", ns) if "atom:" in prefix else element.find(tag)
            if el is not None:
                return el.get(attr, "")
        return ""

    @staticmethod
    def _clean_html(text: str) -> str:
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = re.sub(r"&\w+;", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    @staticmethod
    def _parse_date(date_str: str) -> str:
        if not date_str:
            return ""
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            return dt.isoformat()
        except Exception:
            pass
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).isoformat()
        except Exception:
            return date_str
