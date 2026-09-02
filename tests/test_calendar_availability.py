from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from modules.calendar_availability import (
    _availability_period,
    _requested_duration,
    find_free_slots,
    format_alternatives,
    suggest_alternatives,
)


class CalendarAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.tz = "Europe/Moscow"
        self.zone = ZoneInfo(self.tz)
        self.now = datetime(2026, 9, 2, 8, 0, tzinfo=self.zone)

    def event(self, start_hour, end_hour, *, transparent=False):
        event = {
            "start": {"dateTime": datetime(2026, 9, 3, start_hour, 0, tzinfo=self.zone).isoformat()},
            "end": {"dateTime": datetime(2026, 9, 3, end_hour, 0, tzinfo=self.zone).isoformat()},
        }
        if transparent:
            event["transparency"] = "transparent"
        return event

    def test_availability_period_tomorrow(self):
        start, end, label = _availability_period("когда я свободен завтра", self.tz, self.now)
        self.assertEqual(start, datetime(2026, 9, 3, 0, 0, tzinfo=self.zone))
        self.assertEqual(end, datetime(2026, 9, 4, 0, 0, tzinfo=self.zone))
        self.assertEqual(label, "завтра")

    def test_requested_duration(self):
        self.assertEqual(_requested_duration("найди окно на 2 часа завтра"), timedelta(hours=2))
        self.assertEqual(_requested_duration("когда свободен завтра"), timedelta(hours=1))

    def test_find_free_slots_skips_busy_periods(self):
        start = datetime(2026, 9, 3, 0, 0, tzinfo=self.zone)
        end = datetime(2026, 9, 4, 0, 0, tzinfo=self.zone)
        events = [self.event(9, 10), self.event(11, 12)]
        slots = find_free_slots(events, self.tz, start, end, timedelta(hours=1), now=self.now, limit=4)
        self.assertEqual(slots[0][0], datetime(2026, 9, 3, 10, 0, tzinfo=self.zone))
        self.assertEqual(slots[1][0], datetime(2026, 9, 3, 12, 0, tzinfo=self.zone))

    def test_transparent_event_does_not_block(self):
        start = datetime(2026, 9, 3, 0, 0, tzinfo=self.zone)
        end = datetime(2026, 9, 4, 0, 0, tzinfo=self.zone)
        slots = find_free_slots(
            [self.event(9, 10, transparent=True)],
            self.tz,
            start,
            end,
            timedelta(hours=1),
            now=self.now,
            limit=1,
        )
        self.assertEqual(slots[0][0].hour, 9)

    @patch("modules.calendar_availability._list_events")
    def test_suggest_alternative_after_conflict(self, list_events):
        list_events.return_value = [self.event(15, 16)]
        desired = datetime(2026, 9, 3, 15, 0, tzinfo=self.zone)
        slots = suggest_alternatives(1, self.tz, desired, timedelta(hours=1), limit=2)
        self.assertEqual(slots[0][0], datetime(2026, 9, 3, 16, 0, tzinfo=self.zone))
        self.assertEqual(slots[1][0], datetime(2026, 9, 3, 16, 30, tzinfo=self.zone))
        self.assertIn("16:00–17:00", format_alternatives(slots))


if __name__ == "__main__":
    unittest.main()
