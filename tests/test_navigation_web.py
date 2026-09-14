from tests.web_client import create_test_app, set_test_session
import unittest
from unittest.mock import patch

import web_app
from core.db import get_or_create_google_user
from modules.navigation import RouteEstimate


class NavigationWebTests(unittest.TestCase):
    def setUp(self):
        self.app = create_test_app()
        self.client = self.app.test_client()
        user_id = get_or_create_google_user(
            "navigation-web-user",
            "navigation@example.test",
            "Navigation User",
        )
        with self.client.session_transaction() as session:
            set_test_session(session, user_id)

    @patch("web_app.navigation_provider", return_value="2gis")
    @patch("web_app.navigation_configured", return_value=True)
    def test_navigation_settings_are_saved_and_returned(self, configured, provider):
        response = self.client.post(
            "/api/settings",
            json={
                "navigation": {
                    "enabled": True,
                    "home_address": "Москва, проспект Мира, 1",
                    "office_address": "Москва, Гиляровского, 53",
                    "default_place": "home",
                    "mode": "driving",
                    "arrival_buffer_minutes": 20,
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        navigation = response.get_json()["navigation"]
        self.assertTrue(navigation["enabled"])
        self.assertTrue(navigation["configured"])
        self.assertEqual(navigation["provider"], "2gis")
        self.assertEqual(navigation["home_address"], "Москва, проспект Мира, 1")
        self.assertEqual(navigation["office_address"], "Москва, Гиляровского, 53")
        self.assertEqual(navigation["default_place"], "home")
        self.assertEqual(navigation["mode"], "driving")
        self.assertEqual(navigation["arrival_buffer_minutes"], 20)

    def test_navigation_settings_reject_unknown_mode(self):
        response = self.client.post(
            "/api/settings",
            json={
                "navigation": {
                    "enabled": True,
                    "default_place": "home",
                    "mode": "teleport",
                    "arrival_buffer_minutes": 15,
                }
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_settings")

    @patch("web_app.navigation_configured", return_value=False)
    def test_navigation_test_reports_missing_server_key(self, configured):
        response = self.client.post(
            "/api/navigation/test",
            json={
                "origin": "Москва, проспект Мира, 1",
                "destination": "ВДНХ",
                "mode": "driving",
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"], "navigation_not_configured")

    @patch("web_app.navigation_provider", return_value="2gis")
    @patch("web_app.navigation_configured", return_value=True)
    @patch("web_app.estimate_route")
    def test_navigation_test_returns_route_summary(self, estimate, configured, provider):
        estimate.return_value = RouteEstimate(
            origin="Москва, проспект Мира, 1",
            destination="ВДНХ",
            mode="driving",
            duration_minutes=18,
            distance_meters=7400,
        )
        response = self.client.post(
            "/api/navigation/test",
            json={
                "origin": "Москва, проспект Мира, 1",
                "destination": "ВДНХ",
                "mode": "driving",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["provider"], "2gis")
        self.assertEqual(payload["duration_minutes"], 18)
        self.assertEqual(payload["distance_meters"], 7400)
        estimate.assert_called_once()

    def test_settings_ui_contains_navigation_controls(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        for control in [
            "navigationGroup",
            "navigationEnabled",
            "homeAddress",
            "officeAddress",
            "defaultPlace",
            "travelMode",
            "arrivalBuffer",
            "testNavigation",
        ]:
            self.assertIn(f'id="{control}"', html)


if __name__ == "__main__":
    unittest.main()
