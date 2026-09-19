import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse

import web_app
from tests.web_test_support import web_test_app
from core.db import get_or_create_google_user
from core.location_context import get_current_location, save_current_location
from core.navigation_store import set_navigation_enabled
from modules.navigation import RouteEstimate
from modules.navigation_extra_api import _yandex_route_url


class NavigationWebTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        self.user_id = get_or_create_google_user(
            "navigation-web-user",
            "navigation@example.test",
            "Navigation User",
        )
        set_navigation_enabled(self.user_id, False)
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def test_navigation_is_opt_in_by_default(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["navigation"]["enabled"])

    @patch("web_app.navigation_provider", return_value="OpenRouteService")
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
        self.assertEqual(navigation["provider"], "OpenRouteService")
        self.assertEqual(navigation["home_address"], "Москва, проспект Мира, 1")
        self.assertEqual(navigation["office_address"], "Москва, Гиляровского, 53")
        self.assertEqual(navigation["default_place"], "home")
        self.assertEqual(navigation["mode"], "driving")
        self.assertEqual(navigation["arrival_buffer_minutes"], 20)

    def test_disabling_navigation_clears_live_location(self):
        set_navigation_enabled(self.user_id, True)
        save_current_location(self.user_id, 55.78, 37.63, 25)
        response = self.client.post(
            "/api/settings",
            json={
                "navigation": {
                    "enabled": False,
                    "home_address": "Москва, проспект Мира, 1",
                    "office_address": "Москва, Гиляровского, 53",
                    "default_place": "home",
                    "mode": "driving",
                    "arrival_buffer_minutes": 20,
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["navigation"]["enabled"])
        self.assertIsNone(get_current_location(self.user_id))

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

    def test_navigation_test_is_blocked_when_disabled(self):
        response = self.client.post(
            "/api/navigation/test",
            json={
                "origin": "Москва, проспект Мира, 1",
                "destination": "ВДНХ",
                "mode": "driving",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "navigation_disabled")

    @patch("web_app.navigation_configured", return_value=False)
    def test_navigation_test_reports_missing_server_key(self, configured):
        set_navigation_enabled(self.user_id, True)
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

    @patch("web_app.navigation_provider", return_value="OpenRouteService")
    @patch("web_app.navigation_configured", return_value=True)
    @patch("web_app.estimate_route")
    def test_navigation_test_returns_route_summary(self, estimate, configured, provider):
        set_navigation_enabled(self.user_id, True)
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
        self.assertEqual(payload["provider"], "OpenRouteService")
        self.assertEqual(payload["duration_minutes"], 18)
        self.assertEqual(payload["distance_meters"], 7400)
        estimate.assert_called_once()

    def test_yandex_route_url_normalizes_live_geo_origin(self):
        url = _yandex_route_url(
            "geo:55.7812,37.6331",
            "Москва, Ленинградский проспект, 80",
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["mode"], ["routes"])
        self.assertEqual(
            unquote(query["rtext"][0]),
            "55.7812000,37.6331000~Москва, Ленинградский проспект, 80",
        )
        self.assertNotIn("geo:", url)

    def test_yandex_route_url_preserves_text_origin(self):
        url = _yandex_route_url("Дом", "Офис")
        query = parse_qs(urlparse(url).query)
        self.assertEqual(unquote(query["rtext"][0]), "Дом~Офис")

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

        script = self.client.get("/navigation-extra.js").get_data(as_text=True)
        self.assertIn('textNode.textContent = "Навигация "', script)
        self.assertIn('enabledOption.textContent = "Включена"', script)
        self.assertIn('disabledOption.textContent = "Выключена"', script)
        self.assertIn("Геопозиция не запрашивается", script)


if __name__ == "__main__":
    unittest.main()
