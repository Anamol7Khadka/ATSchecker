"""
company_intel.py — Company reconnaissance module.

Before scraping, profiles each target company to discover:
  1. Which ATS platform they use (Greenhouse, Lever, Workday, etc.)
  2. Their career page URL (following redirects)
  3. Hidden API endpoints from robots.txt
  4. Whether they have a sitemap with job URLs
  5. RSS feeds for career pages

Results are cached in .company_intel.json so future runs are instant.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

# ATS platform detection patterns
ATS_PATTERNS = {
    "greenhouse": [
        re.compile(r"boards\.greenhouse\.io/(\w+)", re.IGNORECASE),
        re.compile(r"greenhouse\.io", re.IGNORECASE),
    ],
    "lever": [
        re.compile(r"jobs\.lever\.co/(\w[\w-]*)", re.IGNORECASE),
        re.compile(r"lever\.co", re.IGNORECASE),
    ],
    "workday": [
        re.compile(r"(\w+)\.wd\d+\.myworkdayjobs\.com", re.IGNORECASE),
        re.compile(r"myworkdayjobs\.com", re.IGNORECASE),
    ],
    "successfactors": [
        re.compile(r"career\w*\.successfactors\.", re.IGNORECASE),
        re.compile(r"successfactors", re.IGNORECASE),
    ],
    "smartrecruiters": [
        re.compile(r"jobs\.smartrecruiters\.com/(\w+)", re.IGNORECASE),
        re.compile(r"smartrecruiters\.com", re.IGNORECASE),
    ],
    "recruitee": [
        re.compile(r"(\w+)\.recruitee\.com", re.IGNORECASE),
    ],
    "personio": [
        re.compile(r"(\w+)\.jobs\.personio\.", re.IGNORECASE),
    ],
}

# Common career page paths to try
CAREER_PATHS = [
    "/careers", "/career", "/jobs", "/karriere",
    "/en/careers", "/de/karriere", "/en/jobs",
    "/careers/", "/career/", "/jobs/",
    "/work-with-us", "/join-us", "/open-positions",
]

CACHE_FILE = ".company_intel.json"
CACHE_EXPIRY_DAYS = 7

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


class CompanyIntel:
    """Discover ATS platforms, career URLs, and scraping strategies for companies."""

    def __init__(self, project_root: str, logger=print):
        self.project_root = project_root
        self.logger = logger
        self.cache_path = os.path.join(project_root, CACHE_FILE)
        self.cache = self._load_cache()

    def profile_companies(self, domains: List[str]) -> Dict[str, Dict[str, Any]]:
        """Profile multiple companies. Returns intel dict keyed by domain."""
        results = {}
        new_profiles = 0

        for domain in domains:
            domain = domain.strip().lower()
            if not domain:
                continue

            # Check cache
            cached = self.cache.get(domain)
            if cached and not self._is_expired(cached):
                results[domain] = cached
                continue

            # Profile this company
            try:
                intel = self._profile_one(domain)
                results[domain] = intel
                self.cache[domain] = intel
                new_profiles += 1

                if intel.get("ats"):
                    self.logger(
                        f"[Intel] {domain}: ATS={intel['ats']}, "
                        f"slug={intel.get('ats_slug', '?')}"
                    )
            except Exception as e:
                self.logger(f"[Intel] {domain}: error — {e}")
                results[domain] = {"domain": domain, "error": str(e)}

            time.sleep(0.5)  # Polite delay

        if new_profiles:
            self._save_cache()
            self.logger(f"[Intel] Profiled {new_profiles} new companies, {len(results)} total")

        return results

    def _profile_one(self, domain: str) -> Dict[str, Any]:
        """Analyze a single company domain."""
        result = {
            "domain": domain,
            "ats": None,
            "ats_slug": None,
            "career_url": None,
            "api_url": None,
            "has_sitemap": False,
            "rss_url": None,
            "robots_career_paths": [],
            "profiled_at": datetime.utcnow().isoformat() + "Z",
        }

        # 1. Check robots.txt for career path hints
        robots_paths = self._parse_robots(domain)
        result["robots_career_paths"] = robots_paths

        # 2. Find the career page (follow redirects)
        career_url, final_url = self._find_career_url(domain)
        result["career_url"] = career_url

        # 3. Detect ATS from final URL
        if final_url:
            ats, slug = self._detect_ats(final_url)
            if ats:
                result["ats"] = ats
                result["ats_slug"] = slug

                # Build direct API URL if possible
                if ats == "greenhouse" and slug:
                    result["api_url"] = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
                elif ats == "lever" and slug:
                    result["api_url"] = f"https://api.lever.co/v0/postings/{slug}?mode=json"

        # 4. Check for sitemap
        result["has_sitemap"] = self._check_sitemap(domain)

        # 5. Check for RSS feed on career page
        if career_url:
            result["rss_url"] = self._discover_rss(career_url)

        return result

    def _parse_robots(self, domain: str) -> List[str]:
        """Parse robots.txt for career-related paths."""
        career_paths = []
        for prefix in [f"https://www.{domain}", f"https://{domain}"]:
            try:
                resp = requests.get(
                    f"{prefix}/robots.txt", timeout=8, headers=HEADERS
                )
                if resp.status_code != 200:
                    continue

                for line in resp.text.splitlines():
                    line = line.strip().lower()
                    if line.startswith(("disallow:", "allow:")):
                        path = line.split(":", 1)[1].strip()
                        if any(hint in path for hint in [
                            "career", "karriere", "job", "stellen",
                            "position", "vacancy", "opening",
                        ]):
                            career_paths.append(path)
                break
            except Exception:
                continue

        return career_paths

    def _find_career_url(self, domain: str) -> tuple:
        """Find the company's career page URL by trying common paths."""
        for prefix in [f"https://www.{domain}", f"https://{domain}",
                       f"https://careers.{domain}", f"https://karriere.{domain}",
                       f"https://jobs.{domain}"]:
            for path in CAREER_PATHS:
                url = f"{prefix}{path}"
                try:
                    resp = requests.head(
                        url, timeout=8, headers=HEADERS,
                        allow_redirects=True,
                    )
                    if resp.status_code < 400:
                        return url, resp.url  # original, final after redirects
                except Exception:
                    continue

        return None, None

    def _detect_ats(self, url: str) -> tuple:
        """Detect which ATS platform a URL points to."""
        for ats_name, patterns in ATS_PATTERNS.items():
            for pattern in patterns:
                match = pattern.search(url)
                if match:
                    slug = match.group(1) if match.lastindex else None
                    return ats_name, slug
        return None, None

    def _check_sitemap(self, domain: str) -> bool:
        """Check if the domain has a sitemap."""
        for prefix in [f"https://www.{domain}", f"https://{domain}"]:
            for path in ["/sitemap.xml", "/sitemap_index.xml"]:
                try:
                    resp = requests.head(
                        f"{prefix}{path}", timeout=5, headers=HEADERS,
                    )
                    if resp.status_code == 200:
                        return True
                except Exception:
                    continue
        return False

    def _discover_rss(self, career_url: str) -> Optional[str]:
        """Try to find RSS feed on career page."""
        try:
            resp = requests.get(career_url, timeout=10, headers=HEADERS)
            if resp.status_code != 200:
                return None

            # Look for RSS/Atom link tags
            rss_match = re.search(
                r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)',
                resp.text, re.IGNORECASE,
            )
            if rss_match:
                rss_url = rss_match.group(1)
                if not rss_url.startswith("http"):
                    from urllib.parse import urljoin
                    rss_url = urljoin(career_url, rss_url)
                return rss_url
        except Exception:
            pass
        return None

    def _load_cache(self) -> Dict:
        """Load cached company intel."""
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cache(self):
        """Save company intel cache."""
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger(f"[Intel] Cache save error: {e}")

    def _is_expired(self, entry: Dict) -> bool:
        """Check if a cache entry is expired."""
        profiled_at = entry.get("profiled_at", "")
        if not profiled_at:
            return True
        try:
            dt = datetime.fromisoformat(profiled_at.replace("Z", "+00:00"))
            return datetime.utcnow().replace(
                tzinfo=dt.tzinfo
            ) - dt > timedelta(days=CACHE_EXPIRY_DAYS)
        except Exception:
            return True

    def get_greenhouse_slugs(self) -> List[str]:
        """Extract discovered Greenhouse slugs from cache."""
        slugs = []
        for domain, intel in self.cache.items():
            if intel.get("ats") == "greenhouse" and intel.get("ats_slug"):
                slugs.append(intel["ats_slug"])
        return slugs

    def get_lever_slugs(self) -> List[str]:
        """Extract discovered Lever slugs from cache."""
        slugs = []
        for domain, intel in self.cache.items():
            if intel.get("ats") == "lever" and intel.get("ats_slug"):
                slugs.append(intel["ats_slug"])
        return slugs
