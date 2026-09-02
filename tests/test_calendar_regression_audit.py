from datetime import datetime, timedelta
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from modules.calendar import _create_event, _detect_category
from modules.calendar_actions import _extract_update_target
from modules.calendar_availability import _availability_period, suggest_alternatives
from modules.calendar_event_features import apply_event_features
from modules.calendar_user import _parse_search_period, _parse_view_period


class CalendarRegressionAuditTests(unittest.TestCase):
    def setUp(self):
        self.tz = "Europe/Moscow"
        self.zone = ZoneInfo(self.tz)
        self.now = datetime(2026, 9, 2, 10, 0, tzinfo=self.zone)

    def test_family_is_personal_not_rest(self):
        self.assertEqual(_detect_category("ужин с семьей"), ("personal", "5"))

    def test_view_period_supports_numeric_date(self):
        start, end, _ = _parse_view_period("что у меня 05.09", self.tz, self.now)
        self.assertEqual(start.date().isoformat(), "2026-09-05")
        self.assertEqual(end.date().isoformat(), "2026-09-06")

    def test_view_period_supports_named_date(self):
        start, end, _ = _parse_view_period("покажи календарь 5 сентября", self.tz, self.now)
        self.assertEqual(start.date().isoformat(), "2026-09-05")
        self.assertEqual(end.date().isoformat(), "2026-09-06")

    def test_search_period_supports_explicit_date(self):
        start, end = _parse_search_period("найди врача 05.09", self.tz, self.now)
        self.assertEqual(start.date().isoformat(), "2026-09-05")
        self.assertEqual(end.date().isoformat(), "2026-09-06")

    def test_after_lunch_restricts_availability_window(self):
        start, end, _ = _availability_period("когда я свободен завтра после обеда", self.tz, self.now)
        self.assertGreaterEqual(start.hour, 13)
        self.assertEqual(end.date().isoformat(), "2026-09-04")

    def test_update_target_drops_source_date(self):
        self.assertEqual(_extract_update_target("перенеси врача завтра на субботу"), "врача")
        self.assertEqual(_extract_update_target("перенеси встречу в пятницу на субботу"), "встречу")

    def test_multiple_reminders_do_not_leave_conjunction_in_title(self):
        event = {
            "summary": "raw",
            "start": {"dateTime": "2026-09-03T10:00:00+03:00", "timeZone": self.tz},
            "end": {"dateTime": "2026-09-03T11:00:00+03:00", "timeZone": self.tz},
        }
        enriched = apply_event_features(event, "создай встречу завтра в 10 напомни за день и за 30 минут")
        self.assertEqual(enriched["summary"], "Встречу")
        self.assertEqual(
            [item["minutes"] for item in enriched["reminders"]["overrides"]],
            [1440, 30],
        )

    @patch("modules.calendar_availability.get_calendar_preferences")
    @patch("modules.calendar_availability._list_events")
    def test_alternatives_continue_to_next_workday(self, list_events, get_prefs):
        get_prefs.return_value = {
            "work_start": "09:00",
            "work_end": "18:00",
            "work_days": [0, 1, 2, 3, 4],
            "buffer_minutes": 0,
        }
        list_events.return_value = [
            {
                "start": {"dateTime": datetime(2026, 9, 3, 17, 0, tzinfo=self.zone).isoformat()},
                "end": {"dateTime": datetime(2026, 9, 3, 18, 0, tzinfo=self.zone).isoformat()},
            }
        ]
        desired = datetime(2026, 9, 3, 17, 0, tzinfo=self.zone)
        slots = suggest_alternatives(1, self.tz, desired, timedelta(hours=1), limit=1)
        self.assertTrue(slots)
        self.assertEqual(slots[0][0].date().isoformat(), "2026-09-04")
        self.assertEqual(slots[0][0].hour, 9)

    @patch("modules.calendar.build")
    @patch("modules.calendar.Credentials.from_authorized_user_info")
    @patch("modules.calendar.get_google_token")
    def test_attendees_receive_google_update_notifications(self, get_token, from_info, build):
        get_token.return_value = {"token": "x"}
        from_info.return_value = MagicMock()
        execute = MagicMock(return_value={"id": "event-1"})
        insert = MagicMock()
        insert.return_value.execute = execute
        service = MagicMock()
        service.events.return_value.insert = insert
        build.return_value = service

        event = {
            "summary": "Встреча",
            "start": {"dateTime": "2026-09-03T10:00:00+03:00", "timeZone": self.tz},
            "end": {"dateTime": "2026-09-03T11:00:00+03:00", "timeZone": self.tz},
            "attendees": [{"email": "a@example.com"}],
        }
        _create_event(1, event)
        kwargs = insert.call_args.kwargs
        self.assertEqual(kwargs.get("sendUpdates"), "all")


if __name__ == "__main__":
    unittest.main()
