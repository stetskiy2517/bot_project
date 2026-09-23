from pathlib import Path
import unittest
import uuid

from core.db import get_or_create_google_user, get_user_appearance_theme
from tests.web_test_support import web_test_app


class AppearanceThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = web_test_app()

    def setUp(self):
        token = uuid.uuid4().hex
        self.user_id = get_or_create_google_user(
            f"appearance-{token}",
            f"appearance-{token}@example.test",
            "Appearance Test",
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id

    def test_default_theme_is_auto(self):
        self.assertEqual(get_user_appearance_theme(self.user_id), "auto")
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["appearance_theme"], "auto")

    def test_theme_can_be_saved(self):
        for theme in ("dark", "light", "auto"):
            response = self.client.post("/api/settings", json={"appearance_theme": theme})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["appearance_theme"], theme)
            self.assertEqual(get_user_appearance_theme(self.user_id), theme)

    def test_invalid_theme_is_rejected_without_mutating_setting(self):
        self.client.post("/api/settings", json={"appearance_theme": "dark"})
        response = self.client.post("/api/settings", json={"appearance_theme": "sepia"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_settings")
        self.assertEqual(get_user_appearance_theme(self.user_id), "dark")

    def test_experimental_appearance_ui_is_not_loaded(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<meta name="color-scheme" content="light" />', html)
        self.assertNotIn("personal-secretary:appearance-theme", html)
        self.assertNotIn("prefers-color-scheme: dark", html)
        self.assertNotIn('/appearance.css', html)
        self.assertNotIn('/appearance.js', html)

        settings = self.client.get("/settings-themes.js").get_data(as_text=True)
        self.assertNotIn('key: "appearance"', settings)
        self.assertNotIn('title: "Оформление"', settings)


if __name__ == "__main__":
    unittest.main()
