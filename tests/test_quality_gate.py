import os
import sys
import unittest
from datetime import datetime
from unittest.mock import Mock, patch


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


from quality_gate import apply_quality_gate, normalize_url, parse_posted_date
from job_scraper import _check_url
from scrapers.base import JobPosting, is_listing_page
from scrapers.google_jobs import GoogleJobsScraper


class QualityGateTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "mode": "strict",
            "batch_min_score": 20,
            "final_min_score": 55,
            "min_title_chars": 8,
            "max_title_chars": 160,
            "min_description_chars": 50,
        }

    def test_normalize_url_removes_tracking_parameters(self):
        normalized = normalize_url("https://example.com/careers/job/123?utm_source=foo&ref=bar&id=9")
        self.assertEqual(normalized, "https://example.com/careers/job/123?id=9")

    def test_rejects_profile_noise(self):
        job = JobPosting(
            title="Senior Data Engineer Profile",
            company="Example",
            location="Berlin",
            url="https://linkedin.com/in/example-person",
            description="This is not a real job posting.",
            source="Google→LinkedIn",
            posted_date=datetime.now().isoformat(),
        )
        result = apply_quality_gate(job, self.config, stage="final")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reject_reason"], "noise_url")

    def test_rejects_xing_profile_noise(self):
        job = JobPosting(
            title="Data Engineer Profile",
            company="Example",
            location="Berlin",
            url="https://www.xing.com/profile/example_person",
            description="This is not a real job posting.",
            source="DDG->XING",
            posted_date=datetime.now().isoformat(),
        )
        result = apply_quality_gate(job, self.config, stage="final")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reject_reason"], "noise_url")

    def test_rejects_zhihu_people_noise(self):
        job = JobPosting(
            title="Feng Da - Zhihu",
            company="Web",
            location="Berlin",
            url="https://www.zhihu.com/people/feng-da-67-58",
            description="This is not a real job posting.",
            source="DDG->Web",
            posted_date=datetime.now().isoformat(),
        )
        result = apply_quality_gate(job, self.config, stage="final")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reject_reason"], "garbage_domain")

    def test_rejects_glassdoor_listing_page(self):
        url = "https://www.glassdoor.com/Jobs/University-of-Magdeburg-Jobs-E420414.htm"
        self.assertTrue(is_listing_page("University of Magdeburg Jobs & Careers - 32 Open Positions", url))
        job = JobPosting(
            title="University of Magdeburg Jobs & Careers - 32 Open Positions",
            company="Glassdoor",
            location="Magdeburg",
            url=url,
            description="A Glassdoor company jobs listing page, not a direct job posting.",
            source="DDG->Glassdoor",
            posted_date=datetime.now().isoformat(),
        )
        result = apply_quality_gate(job, self.config, stage="final")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reject_reason"], "listing_page")

    def test_accepts_glassdoor_job_detail_page(self):
        job = JobPosting(
            title="Werkstudent Data Engineering",
            company="Glassdoor",
            location="Berlin",
            url="https://www.glassdoor.de/job-listing/werkstudent-data-engineering-mwd-example-JV_IC2622109_KO0,32_KE33,40.htm?jl=1009563665458",
            description="Work with Python, data engineering pipelines, backend systems, and analytics workflows in a production environment.",
            source="DDG->Glassdoor",
            posted_date=datetime.now().isoformat(),
        )
        result = apply_quality_gate(job, self.config, stage="final")
        self.assertTrue(result["accepted"])

    def test_accepts_high_signal_job(self):
        job = JobPosting(
            title="Working Student Data Engineering",
            company="Mercedes-Benz AG",
            location="Leipzig",
            url="https://jobs.example.com/careers/data-engineering-working-student?id=42&utm_source=feed",
            description="Build ETL pipelines, work with Python, Spark, data engineering workflows, and backend services in a production environment.",
            source="StepStone",
            posted_date=datetime.now().isoformat(),
        )
        result = apply_quality_gate(job, self.config, stage="final")
        self.assertTrue(result["accepted"])
        self.assertGreaterEqual(result["score"], 55)
        self.assertEqual(job.url, "https://jobs.example.com/careers/data-engineering-working-student?id=42")

    def test_parse_group_date_format(self):
        parsed = parse_posted_date("2026 03 14")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.month, 3)
        self.assertEqual(parsed.day, 14)

    def test_search_aggregator_rejects_generic_zhihu_result(self):
        scraper = GoogleJobsScraper(config={})
        self.assertTrue(
            scraper._is_noise_result(
                "https://www.zhihu.com/question/27533426",
                "Werkstudent和Praktikant的区别？ - 知乎",
                "Generic Q&A page, not a job posting.",
            )
        )

    def test_search_aggregator_allows_glassdoor_job_detail(self):
        scraper = GoogleJobsScraper(config={})
        self.assertFalse(
            scraper._is_noise_result(
                "https://www.glassdoor.de/job-listing/working-student-data-engineering-example-JV_IC2622109_KO0,32_KE33,40.htm?jl=1009563665458",
                "Working Student Data Engineering - glassdoor.de",
                "Apply for this working student data engineering job.",
            )
        )

    @patch("job_scraper.requests.get")
    @patch("job_scraper.requests.head")
    def test_url_verifier_keeps_blocked_glassdoor_job_detail(self, mock_head, mock_get):
        mock_head.return_value = Mock(status_code=403, url="https://www.glassdoor.de/blocked")
        mock_get.return_value = Mock(status_code=403, url="https://www.glassdoor.de/blocked")
        mock_get.return_value.close = Mock()

        job = JobPosting(
            title="Working Student Data Engineering",
            company="Glassdoor",
            location="Berlin",
            url="https://www.glassdoor.de/job-listing/working-student-data-engineering-example-JV_IC2622109_KO0,32_KE33,40.htm?jl=1009563665458",
            description="Apply for this working student data engineering job.",
            source="DDG->Glassdoor",
        )
        result = _check_url(job, {"max_retries_per_task": 0})

        self.assertTrue(result["keep"])
        self.assertEqual(result["status"], "blocked")

    @patch("job_scraper.requests.get")
    @patch("job_scraper.requests.head")
    def test_url_verifier_drops_blocked_non_glassdoor_link(self, mock_head, mock_get):
        mock_head.return_value = Mock(status_code=403, url="https://example.com/jobs/1")
        mock_get.return_value = Mock(status_code=403, url="https://example.com/jobs/1")
        mock_get.return_value.close = Mock()

        job = JobPosting(
            title="Working Student Data Engineering",
            company="Example",
            location="Berlin",
            url="https://example.com/jobs/1",
            description="Apply for this working student data engineering job.",
            source="DDG->Web",
        )
        result = _check_url(job, {"max_retries_per_task": 0})

        self.assertFalse(result["keep"])
        self.assertEqual(result["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
