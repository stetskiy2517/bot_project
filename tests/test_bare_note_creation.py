from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from modules.notes import (
    NOTE_CREATE,
    _split_note_payload,
    create_note_from_text,
    detect_bare_note,
    detect_note_intent,
)
from modules.router import route_text


class BareNoteParsingTests(unittest.TestCase):
    def test_exact_screenshot_phrase_is_a_note(self):
        text = "Список покупок. Бананы, масло сливочное, масло оливковое, масло растительное."
        self.assertEqual(
            detect_bare_note(text),
            (
                "Список покупок",
                "Бананы, масло сливочное, масло оливковое, масло растительное",
            ),
        )
        self.assertEqual(detect_note_intent(text), NOTE_CREATE)
        self.assertEqual(
            _split_note_payload(text),
            (
                "Список покупок",
                "Бананы, масло сливочное, масло оливковое, масло растительное",
            ),
        )

    def test_arbitrary_title_is_supported(self):
        self.assertEqual(
            detect_bare_note("Проект Альфа. Иван ждёт смету"),
            ("Проект Альфа", "Иван ждёт смету"),
        )
        self.assertEqual(
            detect_bare_note("Идеи для отпуска. Испания, машина, отель у моря"),
            ("Идеи для отпуска", "Испания, машина, отель у моря"),
        )

    def test_calendar_like_sentence_is_not_stolen(self):
        phrases = [
            "Встреча завтра. Иван в 15:00",
            "Созвон с Иваном. Завтра в 16:00",
            "Расписание. Что у меня завтра",
            "Покажи календарь. Завтра",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIsNone(detect_bare_note(phrase))

    def test_question_is_not_silently_saved(self):
        self.assertIsNone(detect_bare_note("Проект Альфа. Что там со сметой?"))
        self.assertIsNone(detect_bare_note("Как дела. Расскажи подробнее"))


class BareNoteCommandTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _update(text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=1),
        )

    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    async def test_bare_note_creates_title_and_body(self):
        text = "Список покупок. Бананы, масло сливочное, масло оливковое, масло растительное."
        update = self._update(text)
        context = self._context()
        stored = {
            "note_id": 10,
            "title": "Список покупок",
            "text": "Бананы, масло сливочное, масло оливковое, масло растительное",
            "created_at": "2026-09-13T09:00:00+00:00",
            "updated_at": "2026-09-13T09:00:00+00:00",
        }
        with patch("modules.notes.create_note", return_value=stored) as create:
            handled = await create_note_from_text(update, context, text)

        self.assertTrue(handled)
        create.assert_called_once_with(
            1,
            "Бананы, масло сливочное, масло оливковое, масло растительное",
            title="Список покупок",
        )
        self.assertIn("Список покупок", update.message.reply_text.await_args.args[0])

    async def test_router_sends_bare_note_to_notes_not_calendar(self):
        text = "Проект Альфа. Иван ждёт смету"
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
