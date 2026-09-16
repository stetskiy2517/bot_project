from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core import db
from core.assistant_preferences import save_assistant_preferences
from core.attention_store import list_attention_items, pending_attention_pushes
from modules import daily_review, navigation_notifications


class DailyBriefingSprintTests(unittest.TestCase):
    def setUp(self):
        self.user_id = 992700127
        db.get_or_create_google_user(
            f"briefing-{self.user_id}",
            f"briefing-{self.user_id}@example.test",
            "Briefing Test",
        )
        db.save_user_timezone(self.user_id, "Europe/Moscow")
        with db.db_lock:
            db.conn.execute("DELETE FROM attention_items WHERE user_id=?", (self.user_id,))
            db.conn.execute("DELETE FROM review_deliveries WHERE user_id=?", (self.user_id,))
            db.conn.execute("DELETE FROM assistant_preferences WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    def tearDown(self):
        with db.db_lock:
            for table in ("attention_items", "review_deliveries", "assistant_preferences"):
                db.conn.execute(f"DELETE FROM {table} WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    def test_review_never_starts_live_email_ai_when_auto_analysis_is_off(self):
        now = datetime(2026, 9, 16, 8, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        with (
            patch.object(daily_review, "_list_events", return_value=[]),
            patch.object(daily_review, "email_auto_enabled", return_value=False),
            patch("modules.email_actions.build_email_plan", side_effect=AssertionError("AI must not be called")) as ai_plan,
        ):
            review = daily_review.build_day_review(self.user_id, "morning", now=now)
        ai_plan.assert_not_called()
        self.assertNotIn("Почта:", review["text"])

    def test_evening_review_previews_tomorrow_calendar(self):
        now = datetime(2026, 9, 16, 20, 15, tzinfo=ZoneInfo("Europe/Moscow"))
        tomorrow = {
            "id": "tomorrow-1",
            "summary": "Встреча завтра",
            "start": {"dateTime": "2026-09-17T10:00:00+03:00"},
            "end": {"dateTime": "2026-09-17T11:00:00+03:00"},
        }
        with patch.object(daily_review, "_list_events", side_effect=[[], [tomorrow]]):
            review = daily_review.build_day_review(self.user_id, "evening", now=now)
        self.assertIn("Завтра:", review["text"])
        self.assertIn("10:00 · Встреча завтра", review["text"])

    def test_scheduled_morning_review_is_deduplicated_in_attention_center(self):
        save_assistant_preferences(
            self.user_id,
            {"morning_enabled": True, "morning_time": "08:00", "quiet_enabled": False},
        )
        now = datetime(2026, 9, 16, 5, 10, tzinfo=timezone.utc)
        review = {
            "kind": "morning",
            "date": "2026-09-16",
            "text": "Утренняя сводка\nВ 10:00 встреча.",
        }
        first = daily_review.capture_review_attention(self.user_id, review, now=now)
        second = daily_review.capture_review_attention(self.user_id, review, now=now)
        self.assertIsNotNone(first)
        self.assertEqual(first["attention_id"], second["attention_id"])
        items = list_attention_items(self.user_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_type"], "daily_review")
        self.assertEqual(items[0]["priority"], "info")
        self.assertEqual(items[0]["title"], "Утренняя сводка")

    def test_leave_now_push_is_saved_once_and_not_requeued(self):
        subscription = {"subscription_id": 123}
        with (
            patch.object(navigation_notifications, "list_push_subscriptions", return_value=[subscription]),
            patch.object(navigation_notifications, "send_web_push") as send,
            patch.object(navigation_notifications, "mark_push_success"),
        ):
            sent = navigation_notifications.send_navigation_push_for_user(
                self.user_id,
                title="Пора выезжать",
                body="Встреча в 16:00. Дорога 35 мин + 10 мин запас.",
                tag="navigation-event-77",
            )
        self.assertTrue(sent)
        send.assert_called_once()
        payload = send.call_args.args[1]
        self.assertIn("view=today", payload["notification"]["navigate"])
        self.assertIn("attention=", payload["notification"]["navigate"])
        items = list_attention_items(self.user_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_type"], "navigation_leave_now")
        self.assertEqual(items[0]["source_key"], "event-77")
        self.assertEqual(items[0]["priority"], "high")
        self.assertIsNotNone(items[0]["pushed_at"])
        self.assertEqual(pending_attention_pushes(self.user_id), [])


if __name__ == "__main__":
    unittest.main()
