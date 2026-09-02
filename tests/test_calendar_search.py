from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from modules.calendar_user import (
    _extract_search_query,
    _format_search_results,
    _parse_search_period,
)


class CalendarSearchTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 2, 16, 43, tzinfo=ZoneInfo("Europe/Moscow"))

    def test_extract_search_query(self):
        self.assertEqual(_extract_search_query("когда у меня невролог?"), "невролог")
        self.assertEqual(_extract_search_query("найди встречу с Ивановым"), "с Ивановым")
        self.assertEqual(_extract_search_query("найди встречу с Ивановым в пятницу"), "с Ивановым")

    def test_search_period_defaults_to_one_year(self):
        start, end = _parse_search_period("когда у меня невролог", "Europe/Moscow", self.now)
        self.assertEqual(start, self.now)
        self.assertEqual((end - start).days, 365)

    def test_search_period_can_be_limited_to_day(self):
        start, end = _parse_search_period("найди встречу с Ивановым в пятницу", "Europe/Moscow", self.now)
        self.assertEqual(start.date().isoformat(), "2026-09-04")
        self.assertEqual((end - start).days, 1)

    def test_format_single_result(self):
        events = [{
            "summary": "Невролог",
            "start": {"dateTime": "2026-09-04T19:00:00+03:00"},
        }]
        text = _format_search_results(events, "Europe/Moscow", "невролог")
        self.assertIn("Нашёл событие", text)
        self.assertIn("04.09 · 19:00 — Невролог", text)

    def test_format_multiple_results(self):
        events = [
            {"summary": "Встреча с Ивановым", "start": {"dateTime": "2026-09-04T10:00:00+03:00"}},
            {"summary": "Созвон с Ивановым", "start": {"dateTime": "2026-09-11T12:00:00+03:00"}},
        ]
        text = _format_search_results(events, "Europe/Moscow", "с Ивановым")
        self.assertIn("Нашёл несколько событий", text)
        self.assertIn("Встреча с Ивановым", text)
        self.assertIn("Созвон с Ивановым", text)

    def test_format_empty_results(self):
        text = _format_search_results([], "Europe/Moscow", "невролог")
        self.assertEqual(text, "Не нашёл будущих событий по запросу «невролог».")


if __name__ == "__main__":
    unittest.main()
