import os
import sys
import unittest
from datetime import datetime


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


from quality_gate import apply_quality_gate, normalize_url, parse_posted_date
from scrapers.base import JobPosting


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


if __name__ == "__main__":
    unittest.main()