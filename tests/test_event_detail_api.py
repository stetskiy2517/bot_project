from __future__ import annotations

import unittest
from datetime import datetime

from modules.event_detail_api import (
    _description_with_category,
    _desired_interval,
    _recurrence_key,
    _series_interval_patch,
)


class EventDetailApiTests(unittest.TestCase):
    def test_description_category_is_replaced_without_losing_text(self):
        value = _description_with_category("Заметка\nAI Smart Planner category: work", "family")
        self.assertEqual(value, "Заметка\nAI Smart Planner category: family")

    def test_common_recurrence_rules_are_normalized(self):
        self.assertEqual(_recurrence_key(["RRULE:FREQ=DAILY"]), "daily")
        self.assertEqual(
            _recurrence_key(["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]),
            "weekdays",
        )
        self.assertEqual(_recurrence_key(["RRULE:FREQ=WEEKLY;BYDAY=MO"]), "custom")

    def test_all_day_editor_uses_google_exclusive_end_date(self):
        start, end, all_day, start_part, end_part = _desired_interval(
            {"all_day": True, "start_date": "2026-09-20", "end_date": "2026-09-22"},
            "Europe/Moscow",
        )
        self.assertTrue(all_day)
        self.assertEqual(start_part, {"date": "2026-09-20"})
        self.assertEqual(end_part, {"date": "2026-09-23"})
        self.assertEqual((end - start).days, 3)

    def test_series_time_change_is_applied_relative_to_parent_anchor(self):
        instance = {
            "start": {"dateTime": "2026-09-16T10:00:00+03:00"},
            "end": {"dateTime": "2026-09-16T11:00:00+03:00"},
        }
        parent = {
            "start": {"dateTime": "2026-09-02T10:00:00+03:00"},
            "end": {"dateTime": "2026-09-02T11:00:00+03:00"},
        }
        start_part, end_part = _series_interval_patch(
            instance,
            parent,
            datetime.fromisoformat("2026-09-16T12:00:00+03:00"),
            datetime.fromisoformat("2026-09-16T13:30:00+03:00"),
            False,
            "Europe/Moscow",
        )
        self.assertEqual(start_part["dateTime"], "2026-09-02T12:00:00+03:00")
        self.assertEqual(end_part["dateTime"], "2026-09-02T13:30:00+03:00")


if __name__ == "__main__":
    unittest.main()
