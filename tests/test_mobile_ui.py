from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import Flask

from modules.mobile_ui_api import _task_payload, mobile_ui_api


class MobileUiHelpersTests(unittest.TestCase):
    def test_task_payload_marks_overdue(self):
        now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
        task = {
            "task_id": 7,
            "title": "Отчёт",
            "status": "open",
            "priority": "high",
            "category": "work",
            "due_at": (now - timedelta(minutes=5)).isoformat(),
            "estimate_minutes": 60,
            "flexible": True,
            "calendar_event_id": None,
            "scheduled_start": None,
            "parent_task_id": None,
            "repeat_rule": None,
        }
        payload = _task_payload(task, now_utc=now)
        self.assertTrue(payload["overdue"])
        self.assertEqual(payload["task_id"], 7)

    def test_swipe_navigation_matches_bottom_nav_contract(self):
        source = (
            Path(__file__).resolve().parent.parent / "web" / "swipe-navigation.js"
        ).read_text(encoding="utf-8")
        self.assertIn('["home", "chat", "today", "more"]', source)
        self.assertIn('planner-library-open', source)
        self.assertIn('closeTopSheet', source)
        self.assertIn('library-swipe-row', source)


class MobileUiApiTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.register_blueprint(mobile_ui_api)

        @app.get("/")
        def index():
            return "<html><head></head><body>ok</body></html>"

        self.client = app.test_client()

    def test_root_injects_mobile_assets(self):
        response = self.client.get("/")
        body = response.get_data(as_text=True)
        self.assertIn('/mobile-ui.css', body)
        self.assertIn('/mobile-ui.js', body)
        self.assertIn('/swipe-navigation.js', body)

    def test_swipe_navigation_asset_is_served(self):
        response = self.client.get("/swipe-navigation.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("VIEW_ORDER", response.get_data(as_text=True))

    def test_today_endpoint_is_stable_without_calendar(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 42
        task = {
            "task_id": 1,
            "title": "Подготовить отчёт",
            "status": "open",
            "priority": "high",
            "category": "work",
            "due_at": None,
            "estimate_minutes": 90,
            "flexible": True,
            "calendar_event_id": None,
            "scheduled_start": None,
            "parent_task_id": None,
            "repeat_rule": None,
        }
        review = {"kind": "morning", "date": "2026-09-15", "calendar_ok": False, "text": "Обзор дня", "reminders": []}
        with patch("modules.mobile_ui_api.get_user_timezone", return_value="Europe/Moscow"), patch(
            "modules.mobile_ui_api._list_events", side_effect=RuntimeError("calendar unavailable")
        ), patch("modules.mobile_ui_api.list_planner_tasks", return_value=[task]), patch(
            "modules.mobile_ui_api.task_summary", return_value={"open": 1, "overdue": 0, "high_priority": 1, "scheduled": 0}
        ), patch("modules.mobile_ui_api.build_day_review", return_value=review):
            response = self.client.get("/api/mobile/today")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["calendar_ok"])
        self.assertEqual(payload["tasks"][0]["title"], "Подготовить отчёт")


if __name__ == "__main__":
    unittest.main()