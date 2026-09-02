import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from modules import auth


class StartModuleTests(unittest.IsolatedAsyncioTestCase):
    def _update(self, user_id=42, full_name="Test User"):
        message = SimpleNamespace(reply_text=AsyncMock())
        user = SimpleNamespace(id=user_id, full_name=full_name)
        return SimpleNamespace(message=message, effective_user=user)

    def test_health_endpoint(self):
        app = auth.create_oauth_web_app()
        response = app.test_client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_callback_rejects_missing_code_or_state(self):
        app = auth.create_oauth_web_app()
        response = app.test_client().get("/oauth2callback")
        self.assertEqual(response.status_code, 400)
        self.assertIn("code", response.get_data(as_text=True).lower())

    @patch("modules.auth.consume_oauth_state", return_value=None)
    def test_callback_rejects_used_or_expired_state(self, consume_state):
        app = auth.create_oauth_web_app()
        response = app.test_client().get("/oauth2callback?state=used-state&code=abc")
        self.assertEqual(response.status_code, 400)
        self.assertIn("устарела", response.get_data(as_text=True).lower())

    @patch("modules.auth._oauth_ready", return_value=False)
    @patch("modules.auth.get_onboarding_status", return_value={"google_connected": False, "timezone_set": False, "preferences": {}})
    @patch("modules.auth.ensure_user")
    async def test_start_without_server_oauth_config_explains_problem(self, ensure_user, status, oauth_ready):
        update = self._update()
        await auth.start_command(update, MagicMock())
        ensure_user.assert_called_once_with(42, "Test User")
        text = update.message.reply_text.await_args.args[0]
        self.assertIn("Google Calendar пока не настроен", text)

    @patch("modules.auth._auth_keyboard", return_value=MagicMock())
    @patch("modules.auth._oauth_ready", return_value=True)
    @patch("modules.auth.get_onboarding_status", return_value={"google_connected": False, "timezone_set": False, "preferences": {}})
    @patch("modules.auth.ensure_user")
    async def test_start_new_user_offers_google_authorization(self, ensure_user, status, oauth_ready, keyboard):
        update = self._update()
        await auth.start_command(update, MagicMock())
        text = update.message.reply_text.await_args.args[0]
        self.assertIn("подключи Google Calendar", text)
        self.assertIn("reply_markup", update.message.reply_text.await_args.kwargs)

    @patch("modules.auth.timezone_command", new_callable=AsyncMock)
    @patch("modules.auth.get_onboarding_status", return_value={"google_connected": True, "timezone_set": False, "preferences": {}})
    @patch("modules.auth.ensure_user")
    async def test_start_authorized_user_without_timezone_continues_onboarding(self, ensure_user, status, timezone_command):
        update = self._update()
        context = MagicMock()
        await auth.start_command(update, context)
        first_text = update.message.reply_text.await_args_list[0].args[0]
        self.assertIn("выбери часовой пояс", first_text.lower())
        timezone_command.assert_awaited_once_with(update, context)

    @patch("modules.auth.get_onboarding_status", return_value={
        "google_connected": True,
        "timezone_set": True,
        "preferences": {"work_start": "09:00", "work_end": "18:00", "buffer_minutes": 15},
    })
    @patch("modules.auth.ensure_user")
    async def test_start_fully_configured_user_reports_ready(self, ensure_user, status):
        update = self._update()
        await auth.start_command(update, MagicMock())
        text = update.message.reply_text.await_args.args[0]
        self.assertIn("Календарь готов к работе", text)
        self.assertIn("09:00–18:00", text)
        self.assertIn("15 мин", text)


if __name__ == "__main__":
    unittest.main()
