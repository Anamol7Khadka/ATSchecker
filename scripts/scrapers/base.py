"""
base.py — Abstract base scraper class and JobPosting dataclass.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse, parse_qs


# ── Listing-page filter ──────────────────────────────────────────────
# Patterns that indicate a DDG result is a search/listing page rather
# than an individual job posting.

_LISTING_TITLE_PATTERNS = re.compile(
    r"(?i)"
    r"(?:^\d[\d,.+]*\s+.*\bjobs?\b)"                  # "257 jobs", "1,000+ jobs"
    r"|(?:^\d[\d,.+]*\s+.*\bStellen\b)"                # "25 offene Stellen"
    r"|(?:\bJetzt\s+\d+\s+offene\b)"                   # "Jetzt 500 offene Stellen finden"
    r"|(?:\bFinde\s+\d+\s+aktuelle\b)"                 # "Finde 100 aktuelle Backend Jobs"
    r"|(?:\b\d+\s+offene\s+Stellen\b)"                 # "25 offene Stellen"
    r"|(?:\b\d+\s+neue?\s+Stellen\b)"                  # "30 neue Stellen"
    r"|(?:\b\d+[+]?\s+(?:new|neue?)\))"                # "(30 new)" or "(21 neue)"
    r"|(?:^\d[\d,.]*[+]\s*\w)"                         # "1,000+Developer Jobs"
    r"|(?:\b\d{2,}\s+Jobs?\s*&)"                       # "513 Jobs &" or "389 Jobs &"
    r"|(?:\bJobs?\s+(?:&\s+)?Stellenangebote\b)"       # "Jobs & Stellenangebote"
    r"|(?:\bJobs\s+in\s+\w+\s*[-–|])"                  # "Jobs in Berlin -" or "Jobs in Berlin |"
    r"|(?:\bJobs\s+in\s+\w+\s*$)"                      # title ending with "Jobs in Berlin"
    r"|(?:\bJobs\s+in\s+\w+,\s*\w)"                    # "Jobs in Berlin, Germany"
    r"|(?:\b\w+\s+Jobs\s+in\s+\w+\s*[-–|]\s*(?:Indeed|LinkedIn|StepStone|XING|Glassdoor|Monster))"  # "Python Jobs in Berlin - Indeed"
    r"|(?:[-–|]\s*(?:Indeed|XING|StepStone|Glassdoor|Monster)\s*$)"  # trailing "- Indeed" or "| XING"
    r"|(?:\b(?:Jan(?:uar)?|Feb(?:ruar)?|M[aä]r(?:z)?|Apr(?:il)?|Mai|Jun[ie]?|Jul[iy]?|Aug(?:ust)?|Sep(?:t)?|Okt(?:ober)?|Nov(?:ember)?|Dez(?:ember)?)\s+20\d{2}\b)"  # "Mär 2026", "January 2025"
)

_LISTING_URL_PARAMS = {"q", "keywords", "query", "keyword", "search"}

_SEARCH_PATH_SEGMENTS = {"/search", "/jobs/search", "/job-search", "/jobsuche"}


def is_listing_page(title: str, url: str) -> bool:
    """Return True if a DDG result looks like a search/listing page
    rather than an individual job posting."""
    # ── Title check ──
    if title and _LISTING_TITLE_PATTERNS.search(title):
        return True

    # ── URL check ──
    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/").lower()
        qs = parse_qs(parsed.query)

        # Reject if URL has search-style query params
        if _LISTING_URL_PARAMS & set(qs.keys()):
            return True

        # Reject if the path IS just the listing endpoint
        if any(path.endswith(seg) for seg in _SEARCH_PATH_SEGMENTS):
            return True

        # Site-specific: LinkedIn listing vs detail
        if "linkedin.com" in parsed.netloc:
            # Detail pages look like /jobs/view/12345
            if "/jobs/search" in path or ("/jobs" in path and "/view/" not in path and not re.search(r"/jobs/\d", path)):
                return True

        # Indeed listing pages
        if "indeed" in parsed.netloc:
            if path in ("/jobs", "/q") or path.startswith("/jobs/") and "viewjob" not in path and "rc/clk" not in url:
                if qs:  # has query params → listing
                    return True
    except Exception:
        pass

    return False


@dataclass
class JobPosting:
    title: str
    company: str
    location: str
    url: str
    description: str = ""
    posted_date: Optional[str] = None
    source: str = ""
    job_type: str = ""  # Werkstudent, Internship, Full-time, etc.
    salary: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "description": self.description,
            "posted_date": self.posted_date,
            "source": self.source,
            "job_type": self.job_type,
            "salary": self.salary,
            "tags": self.tags,
            "scraped_at": self.scraped_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "JobPosting":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class BaseScraper(ABC):
    """Abstract base class for all job scrapers."""

    name: str = "base"

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.delay = self.config.get("request_delay_seconds", 2)
        self.max_results = self.config.get("max_results_per_source", 30)
        self.headless = self.config.get("selenium_headless", True)

    @abstractmethod
    def scrape(
        self, city: str, keywords: List[str], job_types: List[str]
    ) -> List[JobPosting]:
        """
        Scrape job listings for the given city, keywords, and job types.
        Returns a list of JobPosting objects.
        """
        pass

    def _build_query(self, keywords: List[str], job_types: List[str]) -> str:
        """Build a search query string from keywords and job types."""
        parts = []
        if job_types:
            parts.append(job_types[0])  # Use primary job type
            if keywords:
                parts.extend(keywords)  # Use all keywords
        return " ".join(parts)

    def __repr__(self):
        return f"<{self.__class__.__name__} scraper>"
