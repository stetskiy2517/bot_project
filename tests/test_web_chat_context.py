import unittest

from core.chat_context import clear_chat_context, recent_chat_messages
from core.web_transport import WebMessage, WebUpdate


class WebChatContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_chat_context()

    def tearDown(self):
        clear_chat_context()

    async def test_deterministic_reply_is_visible_to_follow_up_ai(self):
        message = WebMessage(text="Что у меня завтра?", user_id=42)
        await message.reply_text("Календарь завтра:\n• 15:00 — Встреча с Иваном")

        self.assertEqual(
            recent_chat_messages(42),
            [
                {"role": "user", "content": "Что у меня завтра?"},
                {"role": "assistant", "content": "Календарь завтра: • 15:00 — Встреча с Иваном"},
            ],
        )

    async def test_one_router_request_records_only_one_exchange(self):
        message = WebMessage(text="Покажи напоминания", user_id=42)
        await message.reply_text("Напоминания: таблетка")
        await message.reply_text("Дополнительная подсказка")

        self.assertEqual(len(recent_chat_messages(42)), 2)
        self.assertEqual(recent_chat_messages(42)[-1]["content"], "Напоминания: таблетка")
        self.assertEqual(message.replies, ["Напоминания: таблетка", "Дополнительная подсказка"])

    async def test_transport_without_user_id_does_not_create_context(self):
        message = WebMessage(text="Тест")
        await message.reply_text("Ответ")
        self.assertEqual(recent_chat_messages(42), [])

    def test_web_update_passes_user_identity_into_message(self):
        update = WebUpdate(77, "Алексей", "Покажи календарь")
        self.assertEqual(update.message.user_id, 77)
        self.assertEqual(update.message.text, "Покажи календарь")


if __name__ == "__main__":
    unittest.main()
