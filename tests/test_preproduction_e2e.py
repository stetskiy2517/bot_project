import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from modules import auth, router


class DummyMessage:
    def __init__(self, text=""):
        self.text = text
        self.reply_text = AsyncMock()


class DummyUpdate:
    def __init__(self, user_id=1001, text=""):
        self.effective_user = SimpleNamespace(id=user_id, full_name="Test User")
        self.message = DummyMessage(text)
        self.callback_query = None


class DummyContext:
    def __init__(self):
        self.user_data = {}
        self.args = []


class PreproductionE2ETests(unittest.IsolatedAsyncioTestCase):
    @patch("modules.auth._auth_keyboard", return_value=MagicMock())
    @patch("modules.auth._oauth_ready", return_value=True)
    @patch("modules.auth.get_onboarding_status")
    @patch("modules.auth.ensure_user")
    async def test_start_new_user_offers_google_auth(self, ensure_user, status, oauth_ready, keyboard):
        status.return_value = {"google_connected": False, "timezone_set": False, "preferences": {}}
        update = DummyUpdate()
        context = DummyContext()
        await auth.start_command(update, context)
        ensure_user.assert_called_once_with(1001, "Test User")
        self.assertEqual(update.message.reply_text.await_count, 1)
        self.assertIn("подключи Google Calendar", update.message.reply_text.await_args.args[0])

    @patch("modules.router.create_from_text", new_callable=AsyncMock, return_value=True)
    async def test_router_create_event_full_user_command(self, create):
        update = DummyUpdate(text="создай встречу завтра в 10 на час")
        context = DummyContext()
        await router.handle_text(update, context)
        create.assert_awaited_once()
        self.assertEqual(create.await_args.args[2], "создай встречу завтра в 10 на час")

    @patch("modules.router.view_from_text", new_callable=AsyncMock, return_value=True)
    async def test_router_view_calendar(self, view):
        update = DummyUpdate(text="что у меня завтра")
        context = DummyContext()
        await router.handle_text(update, context)
        view.assert_awaited_once()

    @patch("modules.router.delete_from_text", new_callable=AsyncMock, return_value=True)
    async def test_router_delete_calendar_event(self, delete):
        update = DummyUpdate(text="удали врача завтра")
        context = DummyContext()
        await router.handle_text(update, context)
        delete.assert_awaited_once()

    @patch("modules.router.update_from_text", new_callable=AsyncMock, return_value=True)
    async def test_router_update_calendar_event(self, update_action):
        update = DummyUpdate(text="перенеси врача завтра на субботу")
        context = DummyContext()
        await router.handle_text(update, context)
        update_action.assert_awaited_once()

    @patch("modules.router.free_slots_from_text", new_callable=AsyncMock, return_value=True)
    async def test_router_free_slots(self, free_slots):
        update = DummyUpdate(text="когда я свободен завтра после обеда")
        context = DummyContext()
        await router.handle_text(update, context)
        free_slots.assert_awaited_once()

    def test_health_endpoint_is_alive(self):
        client = auth.create_oauth_web_app().test_client()
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_oauth_rejects_google_error(self):
        client = auth.create_oauth_web_app().test_client()
        response = client.get("/oauth2callback?error=access_denied")
        self.assertEqual(response.status_code, 400)
        self.assertIn("access_denied", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
