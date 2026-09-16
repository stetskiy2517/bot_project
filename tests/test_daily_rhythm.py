from datetime import datetime, timezone
import unittest
from unittest.mock import patch
import uuid
from zoneinfo import ZoneInfo

from core import db
from core.assistant_preferences import save_assistant_preferences
from core.attention_store import dismiss_attention_item, get_attention_by_source
from modules import attention, daily_review
from modules.navigation_notifications import send_navigation_push_for_user


class DailyRhythmTests(unittest.TestCase):
    def setUp(self):
        token = uuid.uuid4().hex
        self.user_id = db.get_or_create_google_user(token, token + "@example.test", "Daily Rhythm")
        db.save_user_timezone(self.user_id, "Europe/Moscow")

    def test_review_never_starts_email_ai_when_auto_analysis_is_disabled(self):
        with (
            patch.object(daily_review, "email_auto_enabled", return_value=False),
            patch("modules.email_actions.build_email_plan") as ai_plan,
        ):
            self.assertEqual(daily_review._email_lines(self.user_id), [])
        ai_plan.assert_not_called()

    def test_enabled_morning_review_creates_one_stable_attention_card(self):
        save_assistant_preferences(
            self.user_id,
            {"morning_enabled": True, "morning_time": "08:00"},
        )
        now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)  # 09:00 Moscow
        with patch.object(attention, "build_day_review", return_value={"text": "Утренняя сводка\nВстреча в 10:00"}) as build:
            self.assertEqual(attention.sync_review_attention(self.user_id, now=now), 1)
            self.assertEqual(attention.sync_review_attention(self.user_id, now=now), 0)
        build.assert_called_once()
        item = get_attention_by_source(self.user_id, "daily_review", "2026-09-16:morning")
        self.assertIsNotNone(item)
        self.assertEqual(item["title"], "Утренняя сводка")
        self.assertEqual(item["priority"], "info")

        self.assertTrue(dismiss_attention_item(self.user_id, item["attention_id"]))
        with patch.object(attention, "build_day_review") as build_again:
            self.assertEqual(attention.sync_review_attention(self.user_id, now=now), 0)
        build_again.assert_not_called()

    def test_evening_review_contains_tomorrow_preview(self):
        now = datetime(2026, 9, 16, 18, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        tomorrow_event = {
            "id": "tomorrow-1",
            "summary": "Созвон с командой",
            "start": {"dateTime": "2026-09-17T09:30:00+03:00"},
            "end": {"dateTime": "2026-09-17T10:00:00+03:00"},
        }
        with (
            patch.object(daily_review, "_list_events", side_effect=[[], [tomorrow_event]]),
            patch.object(daily_review, "find_free_slots", return_value=[]),
            patch.object(daily_review, "_task_lines", return_value=[]),
            patch.object(daily_review, "list_saved_reminders", return_value=[]),
        ):
            review = daily_review.build_day_review(self.user_id, "evening", now=now)
        self.assertIn("Завтра: 1 событий.", review["text"])
        self.assertIn("09:30 · Созвон с командой", review["text"])

    def test_navigation_leave_alert_is_queued_once_in_attention_center(self):
        first = send_navigation_push_for_user(
            self.user_id,
            title="Пора выезжать",
            body="Встреча в 15:00. Дорога 30 мин + 15 мин запас.",
            tag="navigation-event-42",
        )
        second = send_navigation_push_for_user(
            self.user_id,
            title="Пора выезжать",
            body="Встреча в 15:00. Дорога 30 мин + 15 мин запас.",
            tag="navigation-event-42",
        )
        self.assertTrue(first)
        self.assertTrue(second)
        item = get_attention_by_source(self.user_id, "navigation_leave", "event-42")
        self.assertIsNotNone(item)
        self.assertEqual(item["title"], "Пора выезжать")
        self.assertEqual(item["priority"], "high")
        self.assertIsNone(item["pushed_at"])
        self.assertEqual(item["push_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
