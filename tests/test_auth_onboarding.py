import json
import unittest
from unittest.mock import MagicMock, patch

from core.db import consume_oauth_state, get_google_token, save_oauth_state
from modules import auth
from modules.settings import _parse_work_days, _valid_hhmm


class AuthOnboardingTests(unittest.TestCase):
    def test_oauth_state_is_one_time(self):
        save_oauth_state("state-once", 987654321)
        self.assertEqual(consume_oauth_state("state-once"), 987654321)
        self.assertIsNone(consume_oauth_state("state-once"))

    @patch("modules.auth.save_oauth_state")
    @patch("modules.auth.Flow.from_client_config")
    @patch("modules.auth._redirect_uri", return_value="https://example.com/oauth2callback")
    @patch("modules.auth._client_config", return_value={"web": {}})
    def test_authorization_url_persists_generated_state(self, client_config, redirect_uri, from_config, save_state):
        flow = MagicMock()
        flow.authorization_url.return_value = ("https://accounts.google.com/auth", "secure-state")
        from_config.return_value = flow

        url = auth.build_authorization_url(42)

        self.assertEqual(url, "https://accounts.google.com/auth")
        save_state.assert_called_once_with("secure-state", 42)
        kwargs = flow.authorization_url.call_args.kwargs
        self.assertEqual(kwargs["access_type"], "offline")
        self.assertEqual(kwargs["prompt"], "consent")

    @patch("modules.auth._telegram_notify")
    @patch("modules.auth.Flow.from_client_config")
    @patch("modules.auth._redirect_uri", return_value="https://example.com/oauth2callback")
    @patch("modules.auth._client_config", return_value={"web": {}})
    def test_oauth_callback_saves_token(self, client_config, redirect_uri, from_config, notify):
        user_id = 987654322
        save_oauth_state("callback-state", user_id)
        credentials = MagicMock()
        credentials.to_json.return_value = json.dumps({
            "token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client",
            "client_secret": "secret",
            "scopes": ["https://www.googleapis.com/auth/calendar.events"],
        })
        flow = MagicMock()
        flow.credentials = credentials
        from_config.return_value = flow

        app = auth.create_oauth_web_app()
        response = app.test_client().get("/oauth2callback?state=callback-state&code=code123")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_google_token(user_id)["refresh_token"], "refresh-token")
        flow.fetch_token.assert_called_once_with(code="code123")
        notify.assert_called_once()

    def test_settings_parsers(self):
        self.assertTrue(_valid_hhmm("09:00"))
        self.assertFalse(_valid_hhmm("25:00"))
        self.assertEqual(_parse_work_days(["1-5"]), [0, 1, 2, 3, 4])
        self.assertEqual(_parse_work_days(["пн", "ср", "пт"]), [0, 2, 4])


if __name__ == "__main__":
    unittest.main()
