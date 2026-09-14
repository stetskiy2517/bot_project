import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.db import get_or_create_google_user
from modules.life_wheel import build_life_wheel_snapshot, event_category
from modules.reminder_categories import reminder_category
from tests.web_test_support import web_test_app


COLORS = {
    "work": "3",
    "health": "6",
    "rest": "10",
    "travel": "7",
    "family": "4",
    "personal": "5",
    "other": None,
}


def event(summary, start, end, *, category=None, status="confirmed", transparency="opaque"):
    description = f"AI Smart Planner category: {category}" if category else ""
    return {
        "summary": summary,
        "description": description,
        "status": status,
        "transparency": transparency,
        "start": {"dateTime": start.isoformat(), "timeZone": "Europe/Moscow"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Europe/Moscow"},
    }


def reminder(text, remind_at, *, category=None):
    return {
        "text": text,
        "remind_at": remind_at.isoformat(),
        "status": "completed",
        **({"category": category} if category else {}),
    }


class LifeWheelCalculationTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("Europe/Moscow")
        self.now = datetime(2026, 9, 14, 20, 0, tzinfo=self.zone)

    def snapshot(self, events, days=30, reminders=None):
        with patch("modules.life_wheel.get_user_timezone", return_value="Europe/Moscow"), patch(
            "modules.life_wheel.get_category_colors", return_value=COLORS
        ):
            return build_life_wheel_snapshot(
                1,
                days=days,
                now=self.now,
                events=events,
                reminders=[] if reminders is None else reminders,
            )

    def test_category_marker_has_priority(self):
        item = {
            "summary": "Встреча с врачом",
            "description": "AI Smart Planner category: family",
        }
        self.assertEqual(event_category(item), "family")

    def test_legacy_event_uses_existing_category_rules(self):
        self.assertEqual(event_category({"summary": "Запись к стоматологу"}), "health")
        self.assertEqual(event_category({"summary": "Созвон с клиентом"}), "work")

    def test_reminder_uses_same_category_rules_as_calendar(self):
        self.assertEqual(reminder_category({"text": "Позвонить маме"}), "family")
        self.assertEqual(reminder_category({"text": "Позвонить клиенту"}), "work")
        self.assertEqual(reminder_category({"text": "Принять лекарство"}), "health")

    def test_stored_reminder_category_has_priority(self):
        self.assertEqual(
            reminder_category({"text": "Позвонить клиенту", "category": "personal"}),
            "personal",
        )

    def test_relative_score_reflects_regular_activity(self):
        work_one = event(
            "Созвон",
            datetime(2026, 9, 10, 10, 0, tzinfo=self.zone),
            datetime(2026, 9, 10, 11, 0, tzinfo=self.zone),
            category="work",
        )
        work_two = event(
            "Встреча",
            datetime(2026, 9, 12, 14, 0, tzinfo=self.zone),
            datetime(2026, 9, 12, 15, 0, tzinfo=self.zone),
            category="work",
        )
        health = event(
            "Тренировка",
            datetime(2026, 9, 11, 18, 0, tzinfo=self.zone),
            datetime(2026, 9, 11, 19, 0, tzinfo=self.zone),
            category="health",
        )
        result = self.snapshot([work_one, work_two, health])
        by_key = {item["key"]: item for item in result["categories"]}
        self.assertEqual(by_key["work"]["score"], 10.0)
        self.assertGreater(by_key["health"]["score"], 0)
        self.assertLess(by_key["health"]["score"], 10)
        self.assertEqual(by_key["work"]["active_days"], 2)
        self.assertEqual(result["totals"]["events"], 3)
        self.assertEqual(result["totals"]["reminders"], 0)

    def test_reminders_are_counted_by_category(self):
        items = [
            reminder("Позвонить маме", datetime(2026, 9, 12, 20, 0, tzinfo=self.zone)),
            reminder("Принять лекарство", datetime(2026, 9, 13, 22, 0, tzinfo=self.zone)),
        ]
        result = self.snapshot([], reminders=items)
        by_key = {item["key"]: item for item in result["categories"]}
        self.assertEqual(by_key["family"]["reminders"], 1)
        self.assertEqual(by_key["health"]["reminders"], 1)
        self.assertGreater(by_key["family"]["score"], 0)
        self.assertGreater(by_key["health"]["score"], 0)
        self.assertEqual(result["totals"]["reminders"], 2)
        self.assertEqual(result["totals"]["items"], 2)
        self.assertTrue(result["reminders_included"])

    def test_reminders_outside_period_do_not_count(self):
        old = reminder("Позвонить маме", datetime(2026, 7, 1, 20, 0, tzinfo=self.zone))
        result = self.snapshot([], reminders=[old])
        self.assertEqual(result["totals"]["reminders"], 0)

    def test_midnight_end_does_not_add_next_active_day(self):
        item = event(
            "Поздняя работа",
            datetime(2026, 9, 10, 23, 0, tzinfo=self.zone),
            datetime(2026, 9, 11, 0, 0, tzinfo=self.zone),
            category="work",
        )
        result = self.snapshot([item])
        work = next(category for category in result["categories"] if category["key"] == "work")
        self.assertEqual(work["active_days"], 1)

    def test_cancelled_and_transparent_events_do_not_count(self):
        cancelled = event(
            "Отменено",
            datetime(2026, 9, 10, 10, 0, tzinfo=self.zone),
            datetime(2026, 9, 10, 11, 0, tzinfo=self.zone),
            category="work",
            status="cancelled",
        )
        free = event(
            "Свободное",
            datetime(2026, 9, 11, 10, 0, tzinfo=self.zone),
            datetime(2026, 9, 11, 11, 0, tzinfo=self.zone),
            category="rest",
            transparency="transparent",
        )
        result = self.snapshot([cancelled, free])
        self.assertEqual(result["totals"]["events"], 0)
        self.assertEqual(result["totals"]["skipped_events"], 2)

    def test_invalid_period_is_rejected(self):
        with self.assertRaises(ValueError):
            self.snapshot([], days=14)


class LifeWheelWebTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        self.user_id = get_or_create_google_user("life-wheel-user", "wheel@example.test", "Wheel")
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id

    def test_root_loads_life_wheel_script(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<script src="/life-wheel.js"></script>', response.get_data(as_text=True))
        script = self.client.get("/life-wheel.js")
        self.assertEqual(script.status_code, 200)
        text = script.get_data(as_text=True)
        self.assertIn('id = "lifeWheelBtn"', text)
        self.assertIn("lifeWheelPanel", text)
        self.assertIn("/api/assistant/life-wheel", text)
        self.assertIn("напомин", text.lower())

    def test_life_wheel_endpoint_uses_session_user_and_period(self):
        payload = {
            "period": {"days": 90},
            "categories": [],
            "totals": {"events": 0, "reminders": 0, "items": 0},
        }
        with patch("modules.assistant_api.build_life_wheel_snapshot", return_value=payload) as build:
            response = self.client.get("/api/assistant/life-wheel?days=90")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), payload)
        build.assert_called_once_with(self.user_id, days=90)

    def test_life_wheel_endpoint_rejects_unknown_period(self):
        with patch("modules.assistant_api.build_life_wheel_snapshot", side_effect=ValueError("bad period")):
            response = self.client.get("/api/assistant/life-wheel?days=14")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_assistant_request")


if __name__ == "__main__":
    unittest.main()
