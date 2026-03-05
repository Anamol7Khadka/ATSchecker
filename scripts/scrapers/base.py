"""
base.py — Abstract base scraper class and JobPosting dataclass.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


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
