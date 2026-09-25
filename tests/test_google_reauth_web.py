import unittest
from unittest.mock import AsyncMock, patch

import web_app
from core.db import clear_google_token, get_or_create_google_user, save_google_token
from integrations.google_calendar_service import GoogleAuthRequired
from tests.web_test_support import web_test_app


class GoogleReauthWebTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        self.user_id = get_or_create_google_user(
            "reauth-web-user",
            "reauth-web@example.test",
            "Reauth User",
        )
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id

    def test_status_switches_to_disconnected_after_token_is_cleared(self):
        save_google_token(self.user_id, {
            "token": "access",
            "refresh_token": "refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client",
            "client_secret": "secret",
        })
        self.assertTrue(self.client.get("/api/status").get_json()["google_connected"])

        clear_google_token(self.user_id)

        payload = self.client.get("/api/status").get_json()
        self.assertFalse(payload["google_connected"])

    def test_calendar_reauth_error_is_explicit_to_web_client(self):
        with patch.object(
            web_app,
            "route_text",
            new=AsyncMock(side_effect=GoogleAuthRequired("GOOGLE_AUTH_REQUIRED")),
        ):
            response = self.client.post("/api/chat", json={"message": "что у меня сегодня?"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "google_auth_required")
        self.assertIn("Подключи Google заново", response.get_json()["message"])

    def test_frontend_has_persistent_explicit_reauth_mode(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("GOOGLE_REAUTH_KEY", html)
        self.assertIn("googleReauthRequired()", html)
        self.assertIn("planner-google-auth-required", html)
        self.assertIn("Подключить Google заново", html)
        self.assertIn("if (status && status.google_connected === true)", html)
        self.assertIn("login.classList.remove(\"open\")", html)


if __name__ == "__main__":
    unittest.main()
