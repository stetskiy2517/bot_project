from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import production_smoke


class ProductionSmokeTests(unittest.TestCase):
    @patch("scripts.production_smoke._disk_status", return_value={"used_percent": 40.0, "free_mb": 6000})
    @patch("scripts.production_smoke._navigation_status", return_value={"provider": "OpenRouteService", "configured": True})
    @patch("scripts.production_smoke._ai_status", return_value={"configured": True, "state": "healthy"})
    @patch("scripts.production_smoke._database_status", return_value={"status": "ok"})
    @patch("scripts.production_smoke._git_sha", return_value="abc123")
    def test_healthy_report_matches_expected_sha(self, *_mocks):
        report, failures = production_smoke.build_report("abc123")
        self.assertEqual(failures, [])
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["sha"], "abc123")
        self.assertEqual(report["database"]["status"], "ok")

    @patch("scripts.production_smoke._disk_status", return_value={"used_percent": 40.0, "free_mb": 6000})
    @patch("scripts.production_smoke._navigation_status", return_value={"provider": "OpenRouteService", "configured": True})
    @patch("scripts.production_smoke._ai_status", return_value={"configured": True, "state": "healthy"})
    @patch("scripts.production_smoke._database_status", return_value={"status": "ok"})
    @patch("scripts.production_smoke._git_sha", return_value="old-sha")
    def test_stale_deployment_is_critical(self, *_mocks):
        report, failures = production_smoke.build_report("new-sha")
        self.assertEqual(report["status"], "error")
        self.assertTrue(any("does not match" in item for item in failures))

    @patch("scripts.production_smoke._disk_status", return_value={"used_percent": 85.8, "free_mb": 891})
    @patch("scripts.production_smoke._navigation_status", return_value={"provider": "OpenRouteService", "configured": True})
    @patch("scripts.production_smoke._ai_status", return_value={"configured": True, "state": "healthy"})
    @patch("scripts.production_smoke._database_status", return_value={"status": "ok"})
    @patch("scripts.production_smoke._git_sha", return_value="abc123")
    def test_disk_pressure_warns_before_it_becomes_critical(self, *_mocks):
        report, failures = production_smoke.build_report("abc123")
        self.assertEqual(failures, [])
        self.assertEqual(report["status"], "ok")
        self.assertIn("85.8%", report["warnings"][0])

    @patch("scripts.production_smoke._disk_status", return_value={"used_percent": 96.0, "free_mb": 100})
    @patch("scripts.production_smoke._navigation_status", return_value={"provider": "OpenRouteService", "configured": True})
    @patch("scripts.production_smoke._ai_status", return_value={"configured": True, "state": "degraded"})
    @patch("scripts.production_smoke._database_status", return_value={"status": "ok"})
    @patch("scripts.production_smoke._git_sha", return_value="abc123")
    def test_disk_threshold_is_critical(self, *_mocks):
        report, failures = production_smoke.build_report("abc123", max_disk_percent=95.0)
        self.assertEqual(report["status"], "error")
        self.assertTrue(any("disk usage" in item for item in failures))

    @patch("scripts.production_smoke._disk_status", return_value={"used_percent": 40.0, "free_mb": 6000})
    @patch("scripts.production_smoke._navigation_status", side_effect=RuntimeError("optional provider down"))
    @patch("scripts.production_smoke._ai_status", side_effect=RuntimeError("optional AI down"))
    @patch("scripts.production_smoke._database_status", return_value={"status": "ok"})
    @patch("scripts.production_smoke._git_sha", return_value="abc123")
    def test_optional_integrations_do_not_fail_core_health(self, *_mocks):
        report, failures = production_smoke.build_report("abc123")
        self.assertEqual(failures, [])
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["ai"]["state"], "unknown")
        self.assertFalse(report["navigation"]["configured"])


if __name__ == "__main__":
    unittest.main()
