import os
import sys
import unittest
from types import SimpleNamespace


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


import app as app_module
from scripts.scrapers.base import JobPosting


class AppRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        app_module.state["jobs"] = []
        app_module.state["matches"] = []
        app_module.state["ats_reports"] = []
        app_module.state["cv_data"] = None
        app_module.state["gap_analysis"] = None

    def _job(self, title, company, location, url, quality_score=80):
        return JobPosting(
            title=title,
            company=company,
            location=location,
            url=url,
            description="Build robust data and backend systems for production use.",
            source="StepStone",
            posted_date="2026-03-14T12:00:00",
            quality={
                "score": quality_score,
                "bucket": "high" if quality_score >= 80 else "medium",
                "url_status": "alive",
                "normalized_url": url,
            },
        )

    def test_dashboard_renders_grouping_and_quality_summary(self):
        job = self._job("Senior Data Engineer", "Example AG", "Berlin", "https://example.com/jobs/1")
        app_module.state["jobs"] = [job]
        app_module.state["matches"] = [SimpleNamespace(job=job, overall_score=86.0)]

        response = self.client.get("/?group=match-tier")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Excellent Match (80%+)", html)
        self.assertIn("Avg quality", html)

    def test_jobs_api_includes_quality_fields(self):
        job = self._job("Werkstudent Data Engineering", "Example AG", "Leipzig", "https://example.com/jobs/2", quality_score=72)
        app_module.state["jobs"] = [job]

        response = self.client.get("/api/jobs")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("quality", payload)
        self.assertEqual(payload["jobs"][0]["quality_score"], 72)
        self.assertEqual(payload["jobs"][0]["url_status"], "alive")

    def test_job_detail_404_for_invalid_index(self):
        response = self.client.get("/job/999")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()