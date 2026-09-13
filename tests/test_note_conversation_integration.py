from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from core.note_store import create_note, delete_note, get_note
from modules.note_conversation import ACTIVE_NOTE_KEY
from modules.router import route_text


class NoteConversationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    USER_ID = 918273645

    def setUp(self):
        self.created_ids: list[int] = []

    def tearDown(self):
        for note_id in self.created_ids:
            delete_note(self.USER_ID, note_id)

    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    def _update(self, text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=self.USER_ID),
        )

    def _create_shopping_note(self):
        note = create_note(
            self.USER_ID,
            "Бананы\nмасло сливочное\nмасло оливковое",
            title="Список покупок",
        )
        self.created_ids.append(note["note_id"])
        return note

    async def test_screenshot_reverse_append_updates_real_store(self):
        note = self._create_shopping_note()
        context = self._context()
        update = self._update("Добавь воду в список покупок")

        handled = await route_text(update, context)

        self.assertTrue(handled)
        stored = get_note(self.USER_ID, note["note_id"])
        self.assertIn("воду", stored["text"])
        self.assertEqual(context.user_data[ACTIVE_NOTE_KEY]["note_id"], note["note_id"])
        self.assertEqual(
            update.message.reply_text.await_args.args[0],
            "Добавил в «Список покупок»: воду.",
        )

    async def test_screenshot_target_first_then_short_followup(self):
        note = self._create_shopping_note()
        context = self._context()

        first = self._update("Добавь в список покупок шоколад")
        self.assertTrue(await route_text(first, context))

        second = self._update("Добавь воду")
        self.assertTrue(await route_text(second, context))

        stored = get_note(self.USER_ID, note["note_id"])
        self.assertIn("шоколад", stored["text"])
        self.assertIn("воду", stored["text"])
        self.assertEqual(
            second.message.reply_text.await_args.args[0],
            "Добавил в «Список покупок»: воду.",
        )

    async def test_read_note_then_short_followup(self):
        note = self._create_shopping_note()
        context = self._context()

        read = self._update("Что у меня в списке покупок?")
        self.assertTrue(await route_text(read, context))
        self.assertEqual(context.user_data[ACTIVE_NOTE_KEY]["note_id"], note["note_id"])

        followup = self._update("И молоко")
        self.assertTrue(await route_text(followup, context))

        stored = get_note(self.USER_ID, note["note_id"])
        self.assertIn("молоко", stored["text"])

    async def test_short_append_without_context_never_creates_calendar_event(self):
        context = self._context()
        update = self._update("Добавь воду")

        handled = await route_text(update, context)

        self.assertTrue(handled)
        self.assertIn("Куда добавить", update.message.reply_text.await_args.args[0])
        self.assertNotIn(ACTIVE_NOTE_KEY, context.user_data)


if __name__ == "__main__":
    unittest.main()
