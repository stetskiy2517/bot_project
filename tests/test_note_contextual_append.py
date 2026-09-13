from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.note_store import derive_note_title
from modules.notes import NOTE_APPEND, _resolve_append_request, append_note_from_text, detect_note_intent
from modules.router import route_text


class ContextualAppendParsingTests(unittest.TestCase):
    def test_existing_sentence_style_note_gets_clean_title(self):
        self.assertEqual(
            derive_note_title("Список покупок. Бананы, масло сливочное, масло оливковое"),
            "Список покупок",
        )

    def test_append_to_named_note_is_detected_without_word_note(self):
        phrase = "Добавить в список покупок шоколад."
        self.assertEqual(detect_note_intent(phrase), NOTE_APPEND)

    def test_calendar_target_is_not_stolen_by_notes(self):
        self.assertIsNone(detect_note_intent("добавить в календарь встречу завтра"))

    def test_resolver_separates_title_from_new_content(self):
        note = {
            "note_id": 7,
            "title": "Список покупок",
            "text": "Бананы\nмасло сливочное",
            "created_at": "2026-09-13T09:00:00+00:00",
            "updated_at": "2026-09-13T09:00:00+00:00",
        }

        def fake_search(user_id: int, query: str, *, limit: int = 50):
            if query.casefold() in {"список покупок", "список"}:
                return [note]
            return []

        with patch("modules.notes.search_notes", side_effect=fake_search):
            query, addition, matches = _resolve_append_request(
                1,
                "Добавить в список покупок шоколад.",
            )

        self.assertEqual(query, "список покупок")
        self.assertEqual(addition, "шоколад")
        self.assertEqual(matches[0]["note_id"], 7)


class ContextualAppendCommandTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _update(text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=1),
        )

    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    async def test_append_command_updates_matching_note(self):
        update = self._update("Добавить в список покупок шоколад.")
        context = self._context()
        note = {
            "note_id": 7,
            "title": "Список покупок",
            "text": "Бананы\nмасло сливочное",
            "created_at": "2026-09-13T09:00:00+00:00",
            "updated_at": "2026-09-13T09:00:00+00:00",
        }
        updated = {**note, "text": "Бананы\nмасло сливочное\nшоколад"}

        def fake_search(user_id: int, query: str, *, limit: int = 50):
            return [note] if query.casefold() in {"список покупок", "список"} else []

        with (
            patch("modules.notes.search_notes", side_effect=fake_search),
            patch("modules.notes.append_note", return_value=updated) as append,
        ):
            handled = await append_note_from_text(update, context, update.message.text)

        self.assertTrue(handled)
        append.assert_called_once_with(1, 7, "шоколад")
        reply = update.message.reply_text.await_args.args[0]
        self.assertIn("Список покупок", reply)
        self.assertIn("шоколад", reply)

    async def test_router_sends_named_append_to_notes_not_calendar(self):
        update = self._update("Добавить в список покупок шоколад.")
        context = self._context()
        with (
            patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note_handler,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar_create,
        ):
            handled = await route_text(update, context)

        self.assertTrue(handled)
        self.assertEqual(note_handler.await_args.args[3], NOTE_APPEND)
        calendar_create.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
