from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

from flask import Flask

from modules.calendar_location_api import _pending_location_request, calendar_location_api


class MobileComposerContractTests(unittest.TestCase):
    def test_mobile_composer_autogrows_and_tracks_keyboard(self):
        source = (
            Path(__file__).resolve().parent.parent / "web" / "mobile-ui-fixes.js"
        ).read_text(encoding="utf-8")

        self.assertIn('document.createElement("textarea")', source)
        self.assertIn('editor.style.height = "0px"', source)
        self.assertIn('editor.scrollHeight', source)
        self.assertIn('--mobile-composer-height', source)
        self.assertIn('visibleViewportHeight()', source)
        self.assertIn('Math.min(...values)', source)
        self.assertIn('composer-keyboard-open', source)

    def test_location_follow_up_is_wired_into_chat(self):
        source = (
            Path(__file__).resolve().parent.parent / "web" / "mobile-ui-fixes.js"
        ).read_text(encoding="utf-8")

        self.assertIn('/api/mobile/calendar-location?title=', source)
        self.assertIn('Где будет «', source)
        self.assertIn('без трансфера', source)
        self.assertIn('secretary-pending-calendar-location', source)


class CalendarLocationHelperTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)
        self.event = {
            "id": "event_123",
            "summary": "Защита ИПР",
            "created": (self.now - timedelta(seconds=20)).isoformat(),
            "start": {"dateTime": "2026-09-16T11:00:00+03:00"},
            "end": {"dateTime": "2026-09-16T12:00:00+03:00"},
        }

    def _patches(self, events):
        return (
            patch("modules.calendar_location_api.get_navigation_preferences", return_value={"enabled": True}),
            patch("modules.calendar_location_api.navigation_configured", return_value=True),
            patch("modules.calendar_location_api.get_user_timezone", return_value="Europe/Moscow"),
            patch("modules.calendar_location_api._list_events", return_value=events),
        )

    def test_new_locationless_event_requests_destination(self):
        patches = self._patches([self.event])
        with patches[0], patches[1], patches[2], patches[3]:
            result = _pending_location_request(1, "Защита ИПР", now=self.now)

        self.assertIsNotNone(result)
        self.assertEqual(result["event_id"], "event_123")
        self.assertEqual(result["title"], "Защита ИПР")

    def test_event_with_location_does_not_request_destination(self):
        event = {**self.event, "location": "Москва, Тверская, 1"}
        patches = self._patches([event])
        with patches[0], patches[1], patches[2], patches[3]:
            result = _pending_location_request(1, "Защита ИПР", now=self.now)
        self.assertIsNone(result)

    def test_remote_event_does_not_request_destination(self):
        event = {**self.event, "summary": "Защита ИПР онлайн"}
        patches = self._patches([event])
        with patches[0], patches[1], patches[2], patches[3]:
            result = _pending_location_request(1, "Защита ИПР онлайн", now=self.now)
        self.assertIsNone(result)


class CalendarLocationApiTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.register_blueprint(calendar_location_api)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = 7

    def test_location_reply_patches_event_and_syncs_navigation(self):
        service = MagicMock()
        service.events.return_value.get.return_value.execute.return_value = {
            "id": "event_123",
            "summary": "Защита ИПР",
            "start": {"dateTime": "2026-09-16T11:00:00+03:00"},
            "end": {"dateTime": "2026-09-16T12:00:00+03:00"},
        }
        service.events.return_value.patch.return_value.execute.return_value = {
            "id": "event_123",
            "summary": "Защита ИПР",
            "location": "Москва, Ленинградский проспект, 1",
            "start": {"dateTime": "2026-09-16T11:00:00+03:00"},
            "end": {"dateTime": "2026-09-16T12:00:00+03:00"},
        }
        with patch("modules.calendar_location_api._get_calendar_service", return_value=service), patch(
            "modules.calendar_location_api.get_user_timezone", return_value="Europe/Moscow"
        ), patch("modules.calendar_location_api.sync_travel_for_event") as sync:
            response = self.client.post(
                "/api/mobile/calendar-location/event_123",
                json={"location": "Москва, Ленинградский проспект, 1"},
            )

        self.assertEqual(response.status_code, 200)
        service.events.return_value.patch.assert_called_once_with(
            calendarId="primary",
            eventId="event_123",
            body={"location": "Москва, Ленинградский проспект, 1"},
        )
        sync.assert_called_once()
        self.assertEqual(response.get_json()["location"], "Москва, Ленинградский проспект, 1")


if __name__ == "__main__":
    unittest.main()
