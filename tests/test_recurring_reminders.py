from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from core.reminder_store import (
    REMINDER_COMPLETED,
    REMINDER_DELIVERED,
    claim_due_for_push,
    claim_due_reminders,
    complete_push_delivery,
    complete_reminder,
    create_reminder,
    delete_saved_reminder,
)
from modules.reminders import (
    REMINDER_CREATE,
    _reminder_due_at,
    _reminder_title,
    _repeat_rule,
    detect_reminder_intent,
)
from modules.router import route_text


class RecurringReminderParsingTests(unittest.TestCase):
    def test_exact_screenshot_phrase_is_daily_reminder_at_23(self):
        text = "Каждый вечер в 11 напомни выпить таблетку"
        self.assertEqual(detect_reminder_intent(text), REMINDER_CREATE)
        self.assertEqual(_repeat_rule(text), "daily")
        self.assertEqual(_reminder_title(text), "Выпить таблетку")

        due = _reminder_due_at(
            text,
            "Europe/Moscow",
            datetime(2026, 9, 13, 20, 42),
        )
        self.assertIsNotNone(due)
        self.assertEqual(
            (due.year, due.month, due.day, due.hour, due.minute),
            (2026, 9, 13, 23, 0),
        )

    def test_recurring_command_after_and_before_schedule(self):
        cases = [
            ("Каждый день в 9 напомни проверить почту", "daily", (2026, 9, 14, 9, 0)),
            ("Напомни каждый вечер в 11 выпить таблетку", "daily", (2026, 9, 13, 23, 0)),
            ("По будням в 8 напомни принять лекарство", "weekdays", (2026, 9, 14, 8, 0)),
            ("Каждую пятницу в 18 напомни отправить отчёт", "weekly:4", (2026, 9, 18, 18, 0)),
        ]
        now = datetime(2026, 9, 13, 20, 42)
        for text, expected_rule, expected_due in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_reminder_intent(text), REMINDER_CREATE)
                self.assertEqual(_repeat_rule(text), expected_rule)
                due = _reminder_due_at(text, "Europe/Moscow", now)
                self.assertIsNotNone(due)
                self.assertEqual(
                    (due.year, due.month, due.day, due.hour, due.minute),
                    expected_due,
                )

    def test_calendar_event_notification_is_not_stolen(self):
        text = "встреча каждый понедельник в 10 напомни за час"
        self.assertIsNone(detect_reminder_intent(text))


class RecurringReminderRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_screenshot_phrase_never_reaches_calendar_create(self):
        text = "Каждый вечер в 11 напомни выпить таблетку"
        update = SimpleNamespace(
            message=SimpleNamespace(text=text),
            effective_user=SimpleNamespace(id=1),
        )
        context = SimpleNamespace(user_data={})
        with (
            patch("modules.router.handle_reminder_text", new=AsyncMock(return_value=True)) as reminder,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        reminder.assert_awaited_once()
        self.assertEqual(reminder.await_args.args[3], REMINDER_CREATE)
        calendar.assert_not_awaited()


class RecurringReminderStoreTests(unittest.TestCase):
    def setUp(self):
        self.user_id = 930_000_000 + (time.time_ns() % 10_000_000)
        self.zone = ZoneInfo("Europe/Moscow")

    def test_daily_schedule_rolls_forward_after_completion(self):
        first = datetime(2026, 9, 13, 23, 0, tzinfo=self.zone)
        reminder = create_reminder(
            self.user_id,
            "Выпить таблетку",
            first,
            repeat_rule="daily",
            repeat_timezone="Europe/Moscow",
        )
        self.assertEqual(reminder["repeat_rule"], "daily")
        next_local = datetime.fromisoformat(reminder["next_remind_at"]).astimezone(self.zone)
        self.assertEqual(
            (next_local.year, next_local.month, next_local.day, next_local.hour, next_local.minute),
            (2026, 9, 14, 23, 0),
        )

        first_delivery = claim_due_reminders(
            self.user_id,
            now=first + timedelta(minutes=1),
        )
        self.assertEqual(len(first_delivery), 1)
        self.assertEqual(first_delivery[0]["reminder_id"], reminder["reminder_id"])
        self.assertEqual(first_delivery[0]["status"], REMINDER_DELIVERED)

        completed = complete_reminder(self.user_id, reminder["reminder_id"])
        self.assertEqual(completed["status"], REMINDER_COMPLETED)

        second = datetime(2026, 9, 14, 23, 1, tzinfo=self.zone)
        second_delivery = claim_due_reminders(self.user_id, now=second)
        self.assertEqual(len(second_delivery), 1)
        rolled = second_delivery[0]
        self.assertEqual(rolled["reminder_id"], reminder["reminder_id"])
        self.assertEqual(rolled["status"], REMINDER_DELIVERED)
        self.assertIsNone(rolled["completed_at"])
        current_local = datetime.fromisoformat(rolled["remind_at"]).astimezone(self.zone)
        self.assertEqual(
            (current_local.year, current_local.month, current_local.day, current_local.hour, current_local.minute),
            (2026, 9, 14, 23, 0),
        )
        following_local = datetime.fromisoformat(rolled["next_remind_at"]).astimezone(self.zone)
        self.assertEqual(
            (following_local.year, following_local.month, following_local.day, following_local.hour, following_local.minute),
            (2026, 9, 15, 23, 0),
        )

    def test_deleted_recurring_schedule_never_fires_again(self):
        first = datetime(2026, 9, 13, 23, 0, tzinfo=self.zone)
        reminder = create_reminder(
            self.user_id,
            "Выпить таблетку",
            first,
            repeat_rule="daily",
            repeat_timezone="Europe/Moscow",
        )
        self.assertTrue(delete_saved_reminder(self.user_id, reminder["reminder_id"]))
        self.assertEqual(
            claim_due_reminders(self.user_id, now=first + timedelta(days=3)),
            [],
        )

    def test_push_delivery_rolls_same_recurring_schedule_to_next_day(self):
        first = datetime(2026, 9, 13, 23, 0, tzinfo=self.zone)
        reminder = create_reminder(
            self.user_id,
            "Выпить таблетку",
            first,
            repeat_rule="daily",
            repeat_timezone="Europe/Moscow",
        )

        first_claim = claim_due_for_push(
            [self.user_id],
            now=first + timedelta(seconds=1),
            limit=10,
        )
        self.assertEqual(len(first_claim), 1)
        self.assertEqual(first_claim[0]["reminder_id"], reminder["reminder_id"])
        self.assertTrue(complete_push_delivery(reminder["reminder_id"]))

        second_claim = claim_due_for_push(
            [self.user_id],
            now=first + timedelta(days=1, seconds=1),
            limit=10,
        )
        self.assertEqual(len(second_claim), 1)
        self.assertEqual(second_claim[0]["reminder_id"], reminder["reminder_id"])
        second_local = datetime.fromisoformat(second_claim[0]["remind_at"]).astimezone(self.zone)
        self.assertEqual(
            (second_local.year, second_local.month, second_local.day, second_local.hour, second_local.minute),
            (2026, 9, 14, 23, 0),
        )

    def test_one_off_reminder_behavior_is_unchanged(self):
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        reminder = create_reminder(self.user_id, "Разовое", due)
        self.assertIsNone(reminder["repeat_rule"])
        self.assertIsNone(reminder["next_remind_at"])
        claimed = claim_due_reminders(self.user_id, now=datetime.now(timezone.utc))
        self.assertTrue(any(item["reminder_id"] == reminder["reminder_id"] for item in claimed))
        claimed_again = claim_due_reminders(
            self.user_id,
            now=datetime.now(timezone.utc) + timedelta(days=1),
        )
        self.assertFalse(any(item["reminder_id"] == reminder["reminder_id"] for item in claimed_again))


if __name__ == "__main__":
    unittest.main()
