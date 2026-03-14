import os
import sys
import unittest
from unittest.mock import patch


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


from scrapers.rate_limiter import can_query, record_rate_limit, record_success, reset_state


class RateLimiterTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.config = {
            "enable_circuit_breaker": True,
            "circuit_breaker_engines": ["google"],
            "rate_limit_max_429_consecutive": 2,
            "rate_limit_cooldown_seconds": 10,
            "circuit_breaker_max_duration_seconds": 30,
        }

    def tearDown(self):
        reset_state()

    @patch("scrapers.rate_limiter.time.time", return_value=1000.0)
    def test_circuit_opens_after_threshold(self, _mock_time):
        self.assertTrue(can_query("google", self.config))
        self.assertEqual(record_rate_limit("google", self.config, "429"), 0.0)
        self.assertTrue(can_query("google", self.config))

        cooldown = record_rate_limit("google", self.config, "429")
        self.assertGreater(cooldown, 0)
        self.assertFalse(can_query("google", self.config))

    def test_half_open_probe_recovers_after_success(self):
        config = dict(self.config)
        config["rate_limit_max_429_consecutive"] = 1

        with patch("scrapers.rate_limiter.time.time", return_value=1000.0):
            cooldown = record_rate_limit("google", config, "429")
            self.assertEqual(cooldown, 10)
            self.assertFalse(can_query("google", config))

        with patch("scrapers.rate_limiter.time.time", return_value=1011.0):
            self.assertTrue(can_query("google", config))
            self.assertFalse(can_query("google", config))
            record_success("google", config)
            self.assertTrue(can_query("google", config))


if __name__ == "__main__":
    unittest.main()