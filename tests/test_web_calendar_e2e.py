import unittest
from unittest.mock import MagicMock, patch

import web_app


class WebCalendarE2ETests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()
        web_app._user_state.clear()

    def _set_timezone(self):
        response = self.client.post("/api/settings", json={"timezone": "Europe/Moscow"})
        self.assertEqual(response.status_code, 200)

    def _google_mocks(self):
        service = MagicMock()
        service.events.return_value.insert.return_value.execute.return_value = {
            "id": "event-1"
        }
        return service

    def test_web_api_creates_calendar_event_through_google_insert(self):
        self._set_timezone()
        service = self._google_mocks()

        with (
            patch("modules.calendar_actions._find_conflicts", return_value=[]),
            patch("modules.calendar.get_google_token", return_value={"token": "test"}),
            patch("modules.calendar.Credentials.from_authorized_user_info", return_value=MagicMock()),
            patch("modules.calendar.build", return_value=service),
        ):
            response = self.client.post(
                "/api/chat",
                json={"message": "поставь врача 20.09.2030 в 19:00 на час"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["handled"])
        self.assertTrue(any("добавлено" in reply.lower() for reply in payload["replies"]))

        service.events.return_value.insert.assert_called_once()
        call = service.events.return_value.insert.call_args
        self.assertEqual(call.kwargs["calendarId"], "primary")
        event = call.kwargs["body"]
        self.assertIn("Врача", event["summary"])
        self.assertEqual(event["start"]["timeZone"], "Europe/Moscow")
        self.assertEqual(event["end"]["timeZone"], "Europe/Moscow")
        self.assertEqual(event["start"]["dateTime"][:16], "2030-09-20T19:00")
        self.assertEqual(event["end"]["dateTime"][:16], "2030-09-20T20:00")

    def test_web_two_step_create_keeps_pending_state_between_requests(self):
        self._set_timezone()
        service = self._google_mocks()

        first = self.client.post(
            "/api/chat",
            json={"message": "поставь врача 20.09.2030"},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["replies"], ["Во сколько поставить событие?"])

        with (
            patch("modules.calendar_actions._find_conflicts", return_value=[]),
            patch("modules.calendar.get_google_token", return_value={"token": "test"}),
            patch("modules.calendar.Credentials.from_authorized_user_info", return_value=MagicMock()),
            patch("modules.calendar.build", return_value=service),
        ):
            second = self.client.post("/api/chat", json={"message": "19:00"})

        self.assertEqual(second.status_code, 200)
        self.assertTrue(any("добавлено" in reply.lower() for reply in second.get_json()["replies"]))
        service.events.return_value.insert.assert_called_once()

    def test_web_create_without_google_returns_channel_neutral_instruction(self):
        self._set_timezone()
        with (
            patch("modules.calendar_actions._find_conflicts", return_value=[]),
            patch("modules.calendar.get_google_token", return_value=None),
        ):
            response = self.client.post(
                "/api/chat",
                json={"message": "поставь врача 20.09.2030 в 19:00"},
            )

        self.assertEqual(response.status_code, 200)
        replies = " ".join(response.get_json()["replies"])
        self.assertIn("Google Calendar", replies)
        self.assertNotIn("/start", replies)
        self.assertNotIn("Telegram", replies)


if __name__ == "__main__":
    unittest.main()
