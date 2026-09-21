from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from core.db import conn, db_lock, get_or_create_google_user
from core.task_planner_store import create_planner_task, get_planner_task, update_planner_task
from modules.calendar_availability import find_free_slots, suggest_alternatives
from modules.task_recurrence import next_task_due
from tests.web_test_support import web_test_app


class AdversarialApiRegressionTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        stamp = uuid4().hex
        self.user_id = get_or_create_google_user(
            f"adversarial-{stamp}",
            f"adversarial-{stamp}@example.test",
            "Adversarial Test",
        )
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id
            stored.permanent = True

    def tearDown(self):
        with db_lock:
            note_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT note_id FROM notes WHERE user_id=?", (self.user_id,)
                ).fetchall()
            ]
            for note_id in note_ids:
                conn.execute(
                    "DELETE FROM note_metadata WHERE user_id=? AND note_id=?",
                    (self.user_id, note_id),
                )
            conn.execute("DELETE FROM notes WHERE user_id=?", (self.user_id,))
            conn.execute("DELETE FROM tasks WHERE user_id=?", (self.user_id,))
            conn.execute("DELETE FROM users WHERE user_id=?", (self.user_id,))
            conn.execute("DELETE FROM google_accounts WHERE user_id=?", (self.user_id,))
            conn.commit()

    def test_invalid_task_input_returns_400_instead_of_500(self):
        response = self.client.post(
            "/api/tasks",
            json={"title": "Неверный повтор", "repeat_rule": "yearly"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_task_request")

    def test_invalid_note_search_returns_400_instead_of_500(self):
        response = self.client.post("/api/note-tools/search", json={"query": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_note_request")

    def test_missing_note_edit_returns_404_instead_of_500(self):
        response = self.client.patch(
            "/api/note-tools/999999999",
            json={"title": "Нет", "text": "Нет"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "note_not_found")

    def test_indirect_task_parent_cycle_is_rejected(self):
        parent = create_planner_task(self.user_id, "Родитель")
        child = create_planner_task(
            self.user_id,
            "Ребёнок",
            parent_task_id=parent["task_id"],
        )
        with self.assertRaisesRegex(ValueError, "родитель"):
            update_planner_task(
                self.user_id,
                parent["task_id"],
                {"parent_task_id": child["task_id"]},
            )
        saved = get_planner_task(self.user_id, parent["task_id"])
        self.assertIsNone(saved["parent_task_id"])


class AdversarialCalendarRegressionTests(unittest.TestCase):
    def test_availability_buffer_does_not_bleed_from_previous_local_day(self):
        tz = "Europe/Saratov"
        zone = ZoneInfo(tz)
        period_start = datetime(2026, 9, 21, 0, 0, tzinfo=zone)
        period_end = datetime(2026, 9, 21, 3, 0, tzinfo=zone)
        flight = {
            "id": "flight-20",
            "start": {"dateTime": "2026-09-20T22:30:00+03:00"},
            "end": {"dateTime": "2026-09-20T23:00:00+03:00"},
        }
        slots = find_free_slots(
            [flight],
            tz,
            period_start,
            period_end,
            timedelta(hours=1),
            work_start=time(0, 0),
            work_end=time(3, 0),
            work_days=list(range(7)),
            buffer=timedelta(minutes=15),
            now=datetime(2026, 9, 20, 20, 0, tzinfo=zone),
            limit=1,
        )
        self.assertEqual(slots[0][0], period_start)

    @patch("modules.calendar_availability.get_calendar_preferences")
    @patch("modules.calendar_availability._list_events")
    def test_alternatives_can_exclude_event_being_edited(self, list_events, get_prefs):
        tz = "Europe/Moscow"
        zone = ZoneInfo(tz)
        own_event = {
            "id": "edited-event",
            "start": {"dateTime": "2026-09-21T15:00:00+03:00"},
            "end": {"dateTime": "2026-09-21T16:00:00+03:00"},
        }
        list_events.return_value = [own_event]
        get_prefs.return_value = {
            "work_start": "09:00",
            "work_end": "18:00",
            "work_days": list(range(7)),
            "buffer_minutes": 15,
        }
        desired = datetime(2026, 9, 21, 15, 0, tzinfo=zone)
        slots = suggest_alternatives(
            1,
            tz,
            desired,
            timedelta(hours=1),
            exclude_event_ids={"edited-event"},
            limit=1,
        )
        self.assertEqual(slots[0][0], desired)


class AdversarialTaskRecurrenceTests(unittest.TestCase):
    def test_daily_task_preserves_local_clock_across_dst(self):
        # 28 Mar 09:00 Tallinn is 07:00 UTC; 29 Mar 09:00 is 06:00 UTC
        # because daylight saving starts overnight.
        due = datetime(2026, 3, 28, 7, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 28, 8, 0, tzinfo=timezone.utc)
        next_due = next_task_due(
            due,
            "daily",
            now=now,
            timezone_name="Europe/Tallinn",
        )
        self.assertEqual(next_due, datetime(2026, 3, 29, 6, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
