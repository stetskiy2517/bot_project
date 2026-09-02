from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from modules.calendar_user import _format_events, _parse_view_period


class CalendarViewTests(unittest.TestCase):
    def setUp(self):
        self.tz = "Europe/Moscow"
        self.now = datetime(2026, 9, 2, 16, 30, tzinfo=ZoneInfo(self.tz))  # Wednesday

    def test_tomorrow_period(self):
        start, end, label = _parse_view_period("что у меня завтра", self.tz, self.now)
        self.assertEqual(start.date().isoformat(), "2026-09-03")
        self.assertEqual(end.date().isoformat(), "2026-09-04")
        self.assertEqual(label, "завтра")

    def test_upcoming_friday(self):
        start, end, label = _parse_view_period("покажи пятницу", self.tz, self.now)
        self.assertEqual(start.date().isoformat(), "2026-09-04")
        self.assertEqual(end.date().isoformat(), "2026-09-05")
        self.assertEqual(label, "пятницу")

    def test_current_week_means_remaining_week(self):
        start, end, label = _parse_view_period("что на неделе", self.tz, self.now)
        self.assertEqual(start.date().isoformat(), "2026-09-02")
        self.assertEqual(end.date().isoformat(), "2026-09-07")
        self.assertEqual(label, "на этой неделе")

    def test_next_week(self):
        start, end, label = _parse_view_period("покажи следующую неделю", self.tz, self.now)
        self.assertEqual(start.date().isoformat(), "2026-09-07")
        self.assertEqual(end.date().isoformat(), "2026-09-14")
        self.assertEqual(label, "на следующей неделе")

    def test_empty_calendar_message(self):
        start, end, label = _parse_view_period("что у меня завтра", self.tz, self.now)
        self.assertEqual(_format_events([], self.tz, start, end, label), "На завтра событий нет.")

    def test_timed_and_all_day_formatting(self):
        start, end, label = _parse_view_period("что на неделе", self.tz, self.now)
        events = [
            {
                "summary": "Созвон с клиентом",
                "start": {"dateTime": "2026-09-03T10:30:00+03:00"},
            },
            {
                "summary": "День рождения",
                "start": {"date": "2026-09-04"},
            },
        ]
        text = _format_events(events, self.tz, start, end, label)
        self.assertIn("03.09 · 10:30 — Созвон с клиентом", text)
        self.assertIn("04.09 · весь день — День рождения", text)


if __name__ == "__main__":
    unittest.main()
