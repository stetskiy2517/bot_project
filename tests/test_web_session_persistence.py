from __future__ import annotations

from datetime import timedelta
import unittest
from unittest.mock import patch

import web_app
from core.db import get_or_create_google_user


class WebSessionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()

    def test_session_policy_keeps_login_for_ninety_days(self):
        self.assertEqual(
            self.app.permanent_session_lifetime,
            timedelta(days=web_app.WEB_SESSION_LIFETIME_DAYS),
        )
        self.assertEqual(web_app.WEB_SESSION_LIFETIME_DAYS, 90)
        self.assertTrue(self.app.config["SESSION_REFRESH_EACH_REQUEST"])
        self.assertTrue(self.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(self.app.config["SESSION_COOKIE_SAMESITE"], "Lax")

    def test_google_callback_creates_persistent_cookie(self):
        user_id = get_or_create_google_user(
            "persistent-callback-sub",
            "persistent-callback@example.test",
            "Persistent User",
        )
        with patch("web_app.complete_web_signin", return_value=user_id):
            response = self.client.get("/oauth2callback?state=test-state&code=test-code")

        self.assertEqual(response.status_code, 302)
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("Expires=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

        with self.client.session_transaction() as stored:
            self.assertEqual(stored["user_id"], user_id)
            self.assertTrue(stored.permanent)

    def test_existing_session_cookie_is_upgraded_without_relogin(self):
        user_id = get_or_create_google_user(
            "legacy-session-sub",
            "legacy-session@example.test",
            "Legacy Session",
        )
        with self.client.session_transaction() as stored:
            stored["user_id"] = user_id
            stored.permanent = False

        response = self.client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Expires=", response.headers.get("Set-Cookie", ""))
        with self.client.session_transaction() as stored:
            self.assertEqual(stored["user_id"], user_id)
            self.assertTrue(stored.permanent)

    def test_persistent_session_expiry_is_refreshed_on_activity(self):
        user_id = get_or_create_google_user(
            "refresh-session-sub",
            "refresh-session@example.test",
            "Refresh Session",
        )
        with self.client.session_transaction() as stored:
            stored["user_id"] = user_id
            stored.permanent = True

        first = self.client.get("/api/status")
        second = self.client.get("/api/status")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertIn("Expires=", first.headers.get("Set-Cookie", ""))
        self.assertIn("Expires=", second.headers.get("Set-Cookie", ""))

    def test_https_public_url_marks_session_cookie_secure(self):
        with patch.object(web_app, "BASE_URL", "https://assistant.example.test"):
            app = web_app.create_web_app()
            client = app.test_client()
            user_id = get_or_create_google_user(
                "secure-session-sub",
                "secure-session@example.test",
                "Secure Session",
            )
            with patch("web_app.complete_web_signin", return_value=user_id):
                response = client.get("/oauth2callback?state=test-state&code=test-code")

        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)


if __name__ == "__main__":
    unittest.main()
