from datetime import datetime
import unittest
from unittest.mock import Mock, patch

from integrations.speech import normalize_time_format
from modules import calendar, calendar_recurrence


class TranscriptBoundaryTests(unittest.TestCase):
    def test_partial_numeric_tokens_are_not_rewritten(self):
        for text in ("встреча в 19.300", "встреча в 19.3012", "встреча в 15.09.2026", "встреча в 15.09.26", "версия 1.2.3"):
            with self.subTest(text=text):
                self.assertEqual(normalize_time_format(text), text)

    def test_time_followed_by_sentence_punctuation_is_rewritten(self):
        self.assertEqual(normalize_time_format("Встреча в 19.30."), "Встреча в 19:30.")

    def test_invalid_event_durations_do_not_crash_or_create_zero_length_events(self):
        now = datetime(2026, 9, 13, 9, 0)
        for duration in ("на 0 минут", "на 999999999999999999999 часов", "на " + "9" * 4400 + " часов"):
            with self.subTest(length=len(duration)):
                self.assertIsNone(calendar._parse_event_timing("встреча завтра в 15:00 " + duration, now))


class RecurringRollbackTests(unittest.TestCase):
    def test_failed_cleanup_is_logged_and_original_failure_is_preserved(self):
        service = Mock()
        events = service.events.return_value
        events.get.return_value.execute.return_value = {
            "id": "parent",
            "summary": "Audit recurring meeting",
            "recurrence": ["RRULE:FREQ=DAILY"],
            "start": {"dateTime": "2026-09-14T15:00:00+03:00"},
            "end": {"dateTime": "2026-09-14T16:00:00+03:00"},
        }
        events.insert.return_value.execute.return_value = {"id": "new-series"}
        original_error = RuntimeError("original series update failed")
        events.patch.return_value.execute.side_effect = original_error
        events.delete.return_value.execute.side_effect = RuntimeError("rollback failed")
        instance = {
            "recurringEventId": "parent",
            "start": {"dateTime": "2026-09-15T15:00:00+03:00"},
            "end": {"dateTime": "2026-09-15T16:00:00+03:00"},
        }
        with patch.object(calendar_recurrence.logger, "exception") as logged:
            with self.assertRaises(RuntimeError) as raised:
                calendar_recurrence.split_recurring_series_for_update(service, instance, {"summary": "Changed"}, "Europe/Moscow")
        self.assertIs(raised.exception, original_error)
        logged.assert_called_once()
        events.delete.assert_called_once_with(calendarId="primary", eventId="new-series")


if __name__ == "__main__":
    unittest.main()
