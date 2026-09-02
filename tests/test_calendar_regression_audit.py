from datetime import datetime
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from modules.calendar import _detect_category, _create_event
from modules.calendar_availability import _availability_period
from modules.calendar_user import _parse_view_period


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

    def test_after_lunch_restricts_availability_window(self):
        start, end, _ = _availability_period("когда я свободен завтра после обеда", self.tz, self.now)
        self.assertGreaterEqual(start.hour, 13)
        self.assertEqual(end.date().isoformat(), "2026-09-04")

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
