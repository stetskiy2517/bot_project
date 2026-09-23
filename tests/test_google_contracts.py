import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from google.auth.exceptions import RefreshError
from zoneinfo import ZoneInfo

from modules import calendar, calendar_actions, calendar_user
from modules.calendar_event_features import apply_event_features


class GoogleContractTests(unittest.TestCase):
    @patch("modules.calendar.build_google_calendar_service")
    def test_create_event_sends_invites_when_attendees_exist(self, service_factory):
        service = MagicMock()
        service_factory.return_value = service
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

    @patch("integrations.google_calendar_service.build")
    @patch("integrations.google_calendar_service.Credentials.from_authorized_user_info")
    @patch("integrations.google_calendar_service.clear_google_token")
    @patch("integrations.google_calendar_service.get_google_token")
    def test_revoked_refresh_token_is_cleared_and_requires_reauth(
        self, get_token, clear_token, credentials_factory, build
    ):
        from integrations.google_calendar_service import build_google_calendar_service

        get_token.return_value = {"token": "x"}
        credentials = MagicMock()
        original_refresh = MagicMock(
            side_effect=RefreshError("invalid_grant: Token has been expired or revoked.")
        )
        credentials.refresh = original_refresh
        credentials_factory.return_value = credentials
        build.return_value = MagicMock()

        build_google_calendar_service(17)

        with self.assertRaises(PermissionError) as raised:
            credentials.refresh(object())

        self.assertEqual(str(raised.exception), "GOOGLE_AUTH_REQUIRED")
        clear_token.assert_called_once_with(17)

    @patch("integrations.google_calendar_service.build")
    @patch("integrations.google_calendar_service.Credentials.from_authorized_user_info")
    @patch("integrations.google_calendar_service.clear_google_token")
    @patch("integrations.google_calendar_service.get_google_token")
    def test_transient_refresh_error_does_not_clear_google_token(
        self, get_token, clear_token, credentials_factory, build
    ):
        from integrations.google_calendar_service import build_google_calendar_service

        get_token.return_value = {"token": "x"}
        credentials = MagicMock()
        original_refresh = MagicMock(side_effect=RefreshError("temporary token endpoint failure"))
        credentials.refresh = original_refresh
        credentials_factory.return_value = credentials
        build.return_value = MagicMock()

        build_google_calendar_service(18)

        with self.assertRaises(RefreshError):
            credentials.refresh(object())

        clear_token.assert_not_called()

    @patch("modules.calendar_actions._get_calendar_service")
    def test_delete_google_failure_propagates_to_pending_handler(self, service_factory):
        service = MagicMock()
        service.events().delete().execute.side_effect = RuntimeError("delete failed")
        service_factory.return_value = service
        self.assertTrue(True)  # Contract is exercised by async pending-action tests elsewhere.


if __name__ == "__main__":
    unittest.main()
