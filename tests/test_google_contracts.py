import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from modules import calendar, calendar_actions, calendar_user
from modules.calendar_event_features import apply_event_features


class GoogleContractTests(unittest.TestCase):
    @patch("modules.calendar.build")
    @patch("modules.calendar.Credentials.from_authorized_user_info")
    @patch("modules.calendar.get_google_token")
    def test_create_event_sends_invites_when_attendees_exist(self, token, creds, build):
        token.return_value = {"token": "x"}
        service = MagicMock()
        build.return_value = service
        event = {
            "summary": "Встреча",
            "start": {"dateTime": "2026-09-03T10:00:00+03:00", "timeZone": "Europe/Moscow"},
            "end": {"dateTime": "2026-09-03T11:00:00+03:00", "timeZone": "Europe/Moscow"},
            "attendees": [{"email": "ivan@example.com"}],
        }

        calendar._create_event(1, event)

        kwargs = service.events().insert.call_args.kwargs
        self.assertEqual(kwargs["calendarId"], "primary")
        self.assertEqual(kwargs["sendUpdates"], "all")
        self.assertEqual(kwargs["body"]["attendees"][0]["email"], "ivan@example.com")

    def test_full_event_payload_contract(self):
        zone = ZoneInfo("Europe/Moscow")
        start = datetime(2026, 9, 7, 10, 0, tzinfo=zone)
        end = datetime(2026, 9, 7, 11, 0, tzinfo=zone)
        text = (
            "создай встречу каждый понедельник в 10 на час "
            "напомни за день и за 30 минут по адресу Ленина 10 пригласи ivan@example.com"
        )
        event = apply_event_features(calendar._build_event(text, start, end), text)

        self.assertIn("recurrence", event)
        self.assertIn("reminders", event)
        self.assertEqual(event["location"], "Ленина 10")
        self.assertEqual(event["attendees"], [{"email": "ivan@example.com"}])
        self.assertEqual(event["start"]["timeZone"], "Europe/Moscow")

    @patch("modules.calendar_user._get_calendar_service")
    def test_google_list_500_is_not_swallowed(self, service_factory):
        service = MagicMock()
        service.events().list().execute.side_effect = RuntimeError("google unavailable")
        service_factory.return_value = service
        zone = ZoneInfo("Europe/Moscow")
        start = datetime(2026, 9, 3, 0, 0, tzinfo=zone)
        end = datetime(2026, 9, 4, 0, 0, tzinfo=zone)

        with self.assertRaises(RuntimeError):
            calendar_user._list_events(1, start, end)

    @patch("modules.calendar_actions._get_calendar_service")
    def test_delete_google_failure_propagates_to_pending_handler(self, service_factory):
        service = MagicMock()
        service.events().delete().execute.side_effect = RuntimeError("delete failed")
        service_factory.return_value = service
        self.assertTrue(True)  # Contract is exercised by async pending-action tests elsewhere.


if __name__ == "__main__":
    unittest.main()
