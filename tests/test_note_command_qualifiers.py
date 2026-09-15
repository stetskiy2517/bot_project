from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from modules.notes import NOTE_CREATE, detect_note_intent
from modules.router import route_text


class NoteCommandQualifierTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _update(text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=1),
        )

    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    async def test_qualified_note_command_does_not_create_calendar_event(self):
        variants = (
            "Создай обычную заметку: Я каждый вечер в 22:00 принимаю таблетки.",
            "Создай новую заметку: Купить молоко",
            "Запиши личную заметку: позвонить маме",
        )
        for text in variants:
            with self.subTest(text=text):
                self.assertEqual(detect_note_intent(text), NOTE_CREATE)
                update = self._update(text)
                context = self._context()
                with (
                    patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note_handler,
                    patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar_create,
                ):
                    handled = await route_text(update, context)
                self.assertTrue(handled)
                self.assertEqual(note_handler.await_args.args[3], NOTE_CREATE)
                calendar_create.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
