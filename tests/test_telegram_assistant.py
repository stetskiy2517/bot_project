import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.chat_context import append_chat_exchange, clear_chat_context, recent_chat_messages
from handlers.text import handle_message_text


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

    async def test_deterministic_reply_is_recorded_for_later_follow_up(self):
        message = _Message("Что у меня завтра?")
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=42),
            callback_query=None,
        )
        context = _Context()

        async def deterministic(proxy, _context, text=None):
            self.assertEqual(text, "Что у меня завтра?")
            await proxy.message.reply_text("Календарь завтра: встреча в 15:00")
            return True

        with patch("handlers.text.route_text", new=AsyncMock(side_effect=deterministic)):
            handled = await handle_message_text(update, context, message.text)

        self.assertTrue(handled)
        self.assertEqual(message.sent[0][0], "Календарь завтра: встреча в 15:00")
        self.assertEqual(
            recent_chat_messages(42),
            [
                {"role": "user", "content": "Что у меня завтра?"},
                {"role": "assistant", "content": "Календарь завтра: встреча в 15:00"},
            ],
        )

    async def test_unhandled_text_uses_ai_with_existing_shared_history(self):
        message = _Message("А что после неё?")
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=42),
            callback_query=None,
        )
        context = _Context()
        append_chat_exchange(42, "Что у меня завтра?", "Встреча в 15:00")

        with (
            patch("handlers.text.route_text", new=AsyncMock(return_value=False)),
            patch("handlers.text.answer_unhandled", return_value="После неё свободно.") as ai,
        ):
            handled = await handle_message_text(update, context, message.text)

        self.assertTrue(handled)
        ai.assert_called_once()
        call = ai.call_args
        self.assertEqual(call.args[0], "А что после неё?")
        self.assertEqual(call.kwargs["user_id"], 42)
        self.assertEqual(call.kwargs["history"][-1]["content"], "Встреча в 15:00")
        self.assertEqual(message.sent[-1][0], "После неё свободно.")
        self.assertEqual(recent_chat_messages(42)[-1]["content"], "После неё свободно.")

    async def test_no_ai_access_keeps_safe_generic_reply(self):
        message = _Message("непонятный текст")
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=42),
            callback_query=None,
        )
        context = _Context()

        with (
            patch("handlers.text.route_text", new=AsyncMock(return_value=False)),
            patch("handlers.text.answer_unhandled", return_value=None),
        ):
            handled = await handle_message_text(update, context, message.text)

        self.assertTrue(handled)
        self.assertEqual(
            message.sent[-1][0],
            "Не понял команду. Скажи иначе или уточни, что нужно сделать.",
        )
        self.assertEqual(recent_chat_messages(42), [])

    async def test_ai_failure_does_not_break_telegram_transport(self):
        message = _Message("что-то разговорное")
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=42),
            callback_query=None,
        )
        context = _Context()

        with (
            patch("handlers.text.route_text", new=AsyncMock(return_value=False)),
            patch("handlers.text.answer_unhandled", side_effect=RuntimeError("provider down")),
        ):
            handled = await handle_message_text(update, context, message.text)

        self.assertTrue(handled)
        self.assertEqual(
            message.sent[-1][0],
            "Не понял команду. Скажи иначе или уточни, что нужно сделать.",
        )


if __name__ == "__main__":
    unittest.main()
