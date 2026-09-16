from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from modules.event_detail_api import (
    _description_with_category,
    _desired_interval,
    _filtered_conflicts,
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

    def test_linked_managed_travel_is_not_reported_as_own_conflict(self):
        source = {"id": "meeting-1"}
        linked_travel = {
            "id": "travel-1",
            "extendedProperties": {
                "private": {
                    "smartPlannerType": "travel",
                    "smartPlannerManaged": "1",
                    "smartPlannerSourceEventId": "meeting-1",
                }
            },
        }
        real_conflict = {"id": "meeting-2"}
        with patch(
            "modules.event_detail_api._find_conflicts",
            return_value=[linked_travel, real_conflict],
        ):
            result = _filtered_conflicts(
                1,
                datetime.fromisoformat("2026-09-16T12:00:00+03:00"),
                datetime.fromisoformat("2026-09-16T13:00:00+03:00"),
                source,
            )
        self.assertEqual(result, [real_conflict])


if __name__ == "__main__":
    unittest.main()
