import unittest
from unittest.mock import patch

import web_app


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()

    def test_health_does_not_require_telegram(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok", "transport": "web"})

    def test_pwa_shell_and_manifest_are_served(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Personal Secretary", response.get_data(as_text=True))
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertIn("Personal Secretary", manifest.get_data(as_text=True))

    def test_empty_chat_message_is_rejected(self):
        response = self.client.post("/api/chat", json={"message": "  "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "empty_message")

    def test_chat_uses_shared_planner_router(self):
        async def fake_route(update, context, text=None):
            self.assertEqual(text, "что у меня завтра")
            await update.message.reply_text("Завтра одна встреча")
            return True

        with patch("web_app.route_text", side_effect=fake_route):
            response = self.client.post("/api/chat", json={"message": "что у меня завтра"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["handled"])
        self.assertEqual(response.get_json()["replies"], ["Завтра одна встреча"])

    def test_settings_validate_work_hours(self):
        response = self.client.post(
            "/api/settings",
            json={"work_start": "18:00", "work_end": "09:00"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_settings")

    def test_google_auth_endpoint_returns_url(self):
        with patch("web_app.build_authorization_url", return_value="https://accounts.google.test/auth"):
            response = self.client.get("/api/google/auth")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["url"], "https://accounts.google.test/auth")


if __name__ == "__main__":
    unittest.main()
