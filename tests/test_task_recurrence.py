from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from modules.task_recurrence import create_next_recurring_task, next_task_due


class TaskRecurrenceTests(unittest.TestCase):
    def test_daily_rule_skips_missed_occurrences(self):
        due = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)
        now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            next_task_due(due, "daily", now=now),
            datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc),
        )

    def test_weekly_rule_keeps_clock_time(self):
        due = datetime(2026, 9, 8, 18, 30, tzinfo=timezone.utc)
        now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            next_task_due(due, "weekly", now=now),
            datetime(2026, 9, 15, 18, 30, tzinfo=timezone.utc),
        )

    def test_monthly_rule_clamps_month_end(self):
        due = datetime(2026, 1, 31, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(
            next_task_due(due, "monthly", now=now),
            datetime(2026, 2, 28, 10, 0, tzinfo=timezone.utc),
        )

    def test_unknown_rule_is_not_recreated(self):
        self.assertIsNone(
            next_task_due(
                datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc),
                "sometimes",
                now=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
            )
        )

    @patch("modules.task_recurrence.create_planner_task")
    def test_next_instance_does_not_copy_calendar_link(self, create):
        create.return_value = {"task_id": 10}
        completed = {
            "title": "Еженедельный отчёт",
            "description": "Проверить цифры и приложить ссылку.",
            "due_at": "2026-09-15T09:00:00+00:00",
            "repeat_rule": "weekly",
            "priority": "high",
            "category": "work",
            "estimate_minutes": 45,
            "flexible": True,
            "parent_task_id": None,
            "calendar_event_id": "old-google-event",
        }
        result = create_next_recurring_task(
            7,
            completed,
            now=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result, {"task_id": 10})
        create.assert_called_once()
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["repeat_rule"], "weekly")
        self.assertEqual(kwargs["description"], "Проверить цифры и приложить ссылку.")
        self.assertNotIn("calendar_event_id", kwargs)
        self.assertEqual(kwargs["due_at"], datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
