from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from modules.calendar_event_features import (
    _extract_attendees,
    _extract_location,
    _recurrence_rule,
    _reminder_minutes,
    apply_event_features,
    build_all_day_event,
    is_all_day,
)
from modules.router import _needs_time


class CalendarEventFeaturesTests(unittest.TestCase):
    def test_all_day_detection(self):
        self.assertTrue(is_all_day("создай отпуск завтра на весь день"))
        self.assertTrue(is_all_day("день рождения Ивана завтра"))
        self.assertFalse(is_all_day("день рождения Ивана завтра в 19:00"))
        self.assertFalse(_needs_time("день рождения Ивана завтра"))

    def test_build_all_day_event(self):
        now = datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        event = build_all_day_event("создай день рождения Ивана завтра", "Europe/Moscow", now)
        self.assertEqual(event["start"]["date"], "2026-09-03")
        self.assertEqual(event["end"]["date"], "2026-09-04")
        self.assertIn("День рождения Ивана", event["summary"])

    def test_reminders(self):
        self.assertEqual(_reminder_minutes("напомни за день и за 30 минут"), [1440, 30])
        self.assertEqual(_reminder_minutes("напомни за час"), [60])

    def test_recurrence(self):
        self.assertEqual(_recurrence_rule("созвон каждый понедельник в 10"), "RRULE:FREQ=WEEKLY;BYDAY=MO")
        self.assertEqual(_recurrence_rule("тренировка ежедневно в 8"), "RRULE:FREQ=DAILY")
        self.assertEqual(_recurrence_rule("отчет каждую неделю в пятницу"), "RRULE:FREQ=WEEKLY")

    def test_location(self):
        self.assertEqual(
            _extract_location("встреча завтра в 10 по адресу Ленина 10 напомни за час"),
            "Ленина 10",
        )

    def test_attendees(self):
        attendees = _extract_attendees("встреча с a@example.com и Boss@Example.com")
        self.assertEqual(attendees, [{"email": "a@example.com"}, {"email": "boss@example.com"}])

    def test_apply_features(self):
        event = {
            "summary": "raw",
            "start": {"dateTime": "2026-09-03T10:00:00+03:00", "timeZone": "Europe/Moscow"},
            "end": {"dateTime": "2026-09-03T11:00:00+03:00", "timeZone": "Europe/Moscow"},
        }
        enriched = apply_event_features(
            event,
            "создай встречу завтра в 10 по адресу Ленина 10 напомни за час пригласи a@example.com",
        )
        self.assertEqual(enriched["location"], "Ленина 10")
        self.assertEqual(enriched["attendees"], [{"email": "a@example.com"}])
        self.assertEqual(enriched["reminders"]["overrides"][0]["minutes"], 60)
        self.assertNotIn("a@example.com", enriched["summary"])


if __name__ == "__main__":
    unittest.main()
