from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
import uuid
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.db import get_or_create_google_user
from core.task_planner_store import create_planner_task
from modules.calendar_availability import find_free_slots
from tests.web_test_support import web_test_app


class BugHuntWaveTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = web_test_app()

    def setUp(self):
        token = uuid.uuid4().hex
        self.user_id = get_or_create_google_user(
            f"bug-hunt-wave-{token}",
            f"bug-hunt-wave-{token}@example.test",
            "Bug Hunt",
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as stored:
            stored.clear()
            stored["user_id"] = self.user_id

    def test_01_free_slot_rounding_never_returns_past_half_hour(self):
        zone = ZoneInfo("Europe/Moscow")
        now = datetime(2026, 9, 21, 10, 30, 45, tzinfo=zone)
        slots = find_free_slots(
            [],
            "Europe/Moscow",
            datetime(2026, 9, 21, 9, 0, tzinfo=zone),
            datetime(2026, 9, 21, 18, 0, tzinfo=zone),
            timedelta(minutes=30),
            work_start=time(9, 0),
            work_end=time(18, 0),
            work_days=[0],
            now=now,
            limit=1,
        )
        self.assertEqual(slots[0][0], datetime(2026, 9, 21, 11, 0, tzinfo=zone))

    def test_02_invalid_parent_task_filter_is_400(self):
        response = self.client.get("/api/tasks?parent_task_id=nope")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_task_filter")

    def test_03_invalid_task_create_is_400(self):
        response = self.client.post("/api/tasks", json={"title": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_task")

    def test_04_invalid_task_update_is_400(self):
        task = create_planner_task(self.user_id, "Bug hunt task")
        response = self.client.patch(
            f"/api/tasks/{task['task_id']}",
            json={"priority": "impossible"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_task")

    def test_05_invalid_navigation_buffers_are_400(self):
        response = self.client.put(
            "/api/navigation/buffers",
            json={"parking_buffer_minutes": -1, "walking_buffer_minutes": 0},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_navigation_buffers")

    def test_06_invalid_navigation_origin_choice_is_400(self):
        pending = [{
            "event_id": "evt-1",
            "timezone": "Europe/Moscow",
            "destination": "Москва",
        }]
        with patch("modules.navigation_extra_api.list_pending_navigation_origins", return_value=pending):
            response = self.client.post(
                "/api/navigation/origin-request",
                json={"event_id": "evt-1", "choice": "teleport"},
            )
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(response.get_json()["error"], "invalid_navigation_origin")

    def test_07_missing_file_is_400_not_500(self):
        with patch("modules.file_ingest_api.has_ai_access", return_value=True):
            response = self.client.post("/api/files/analyze", data={})
        self.assertEqual(response.status_code, 400)

    def test_08_invalid_file_calendar_proposal_is_400_not_500(self):
        response = self.client.post("/api/files/calendar", json={"event": None})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_file_request")

    def test_09_route_window_is_opened_before_async_request(self):
        source = Path("web/navigation-extra.js").read_text(encoding="utf-8")
        start = source.index("async function openNextRoute")
        block = source[start:source.index("\n  function ensureOriginCard", start)]
        self.assertLess(block.index('window.open("about:blank"'), block.index('await api("/api/navigation/next-route")'))

    def test_10_reminder_editor_load_failure_is_visible(self):
        source = Path("web/reminder-editor.js").read_text(encoding="utf-8")
        self.assertNotIn('openEditor(Number(edit.dataset.reminderEdit)).catch(() => {})', source)
        self.assertIn('reminderEditorFeedback', source)
        self.assertIn('"Не удалось открыть напоминание."', source)


if __name__ == "__main__":
    import unittest
    unittest.main()
