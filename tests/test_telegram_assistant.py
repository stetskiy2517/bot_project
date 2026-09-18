import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.chat_context import append_chat_exchange, clear_chat_context, recent_chat_messages
from handlers.text import handle_message_text
from modules.ai_assistant import UNHANDLED_WEB_MESSAGE_EN


class _Message:
    def __init__(self, text=""):
        self.text = text
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))


class _Context:
    def __init__(self):
        self.user_data = {}
        self.args = []


class TelegramAssistantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_chat_context()

    def tearDown(self):
        clear_chat_context()

    def _update(self, text, user_id=42):
        return SimpleNamespace(
            message=_Message(text),
            effective_user=SimpleNamespace(id=user_id),
            callback_query=None,
        )

    async def test_deterministic_reply_is_recorded_for_later_follow_up(self):
        update = self._update("Что у меня завтра?")
        context = _Context()

        async def deterministic(proxy, _context, text=None):
            self.assertEqual(text, "Что у меня завтра?")
            await proxy.message.reply_text("Календарь завтра: встреча в 15:00")
            return True

        with patch("handlers.text.route_text", new=AsyncMock(side_effect=deterministic)):
            handled = await handle_message_text(update, context, update.message.text)

        self.assertTrue(handled)
        self.assertEqual(update.message.sent[0][0], "Календарь завтра: встреча в 15:00")
        self.assertEqual(
            recent_chat_messages(42),
            [
                {"role": "user", "content": "Что у меня завтра?"},
                {"role": "assistant", "content": "Календарь завтра: встреча в 15:00"},
            ],
        )

    async def test_multiline_text_reaches_deterministic_router_unchanged(self):
        original = "заметка План поездки\nКупить билеты\nПроверить отель"
        update = self._update(original)
        context = _Context()

        async def deterministic(proxy, _context, text=None):
            self.assertEqual(text, original)
            await proxy.message.reply_text("Заметка сохранена")
            return True

        with patch("handlers.text.route_text", new=AsyncMock(side_effect=deterministic)):
            await handle_message_text(update, context, original)

        self.assertEqual(update.message.sent[-1][0], "Заметка сохранена")

    async def test_unhandled_text_uses_ai_with_existing_shared_history(self):
        update = self._update("А что после неё?")
        context = _Context()
        append_chat_exchange(42, "Что у меня завтра?", "Встреча в 15:00")

        with (
            patch("handlers.text.route_text", new=AsyncMock(return_value=False)),
            patch("handlers.text.answer_unhandled", return_value="После неё свободно.") as ai,
        ):
            handled = await handle_message_text(update, context, update.message.text)

        self.assertTrue(handled)
        ai.assert_called_once()
        call = ai.call_args
        self.assertEqual(call.args[0], "А что после неё?")
        self.assertEqual(call.kwargs["user_id"], 42)
        self.assertEqual(call.kwargs["history"][-1]["content"], "Встреча в 15:00")
        self.assertEqual(update.message.sent[-1][0], "После неё свободно.")
        self.assertEqual(recent_chat_messages(42)[-1]["content"], "После неё свободно.")

    async def test_unknown_english_text_reaches_ai_instead_of_router_fallback(self):
        update = self._update("What should I do after that?")
        context = _Context()

        with (
            patch("handlers.text.route_text", new=AsyncMock(return_value=False)),
            patch("handlers.text.answer_unhandled", return_value="You are free after 4 PM.") as ai,
        ):
            handled = await handle_message_text(update, context, update.message.text)

        self.assertTrue(handled)
        ai.assert_called_once_with(
            "What should I do after that?",
            user_id=42,
            history=[],
        )
        self.assertEqual(update.message.sent[-1][0], "You are free after 4 PM.")

    async def test_no_ai_access_keeps_localized_english_fallback(self):
        update = self._update("something unclear")
        context = _Context()

        with (
            patch("handlers.text.route_text", new=AsyncMock(return_value=False)),
            patch("handlers.text.answer_unhandled", return_value=None),
        ):
            handled = await handle_message_text(update, context, update.message.text)

        self.assertTrue(handled)
        self.assertEqual(update.message.sent[-1][0], UNHANDLED_WEB_MESSAGE_EN)
        self.assertEqual(recent_chat_messages(42), [])

    async def test_router_failure_isolated_and_localized(self):
        update = self._update("show me tomorrow")
        context = _Context()

        with (
            patch("handlers.text.route_text", new=AsyncMock(side_effect=RuntimeError("boom"))),
            patch("handlers.text.answer_unhandled") as ai,
        ):
            handled = await handle_message_text(update, context, update.message.text)

        self.assertTrue(handled)
        ai.assert_not_called()
        self.assertEqual(
            update.message.sent[-1][0],
            "I couldn't process the message. Please try again.",
        )


if __name__ == "__main__":
    unittest.main()
