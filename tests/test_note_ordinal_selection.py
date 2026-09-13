from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.note_store import create_note, delete_note, get_note, list_notes
from modules.note_conversation import (
    ACTIVE_NOTE_KEY,
    NOTE_LIST_CONTEXT_KEY,
    note_selection_index,
)
from modules.router import route_text


class NoteOrdinalParsingTests(unittest.TestCase):
    def test_screenshot_phrases_are_recognised(self):
        self.assertEqual(note_selection_index("Первая заметка"), 0)
        self.assertEqual(note_selection_index("Нет, выведи первую заметку в списке"), 0)
        self.assertEqual(note_selection_index("Покажи первую заметку"), 0)

    def test_more_ordinal_forms_are_supported(self):
        self.assertEqual(note_selection_index("Открой вторую заметку"), 1)
        self.assertEqual(note_selection_index("Покажи заметку номер 3"), 2)
        self.assertEqual(note_selection_index("Дай десятую заметку из списка"), 9)

    def test_unrelated_or_negated_phrases_are_not_captured(self):
        self.assertIsNone(note_selection_index("Не показывай первую заметку"))
        self.assertIsNone(note_selection_index("Первая встреча завтра"))
        self.assertIsNone(note_selection_index("Задача открыть первую заметку"))


class NoteOrdinalRouterPriorityTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _update(text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=1),
        )

    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    async def test_screenshot_correction_is_handled_before_tasks(self):
        update = self._update("Нет выведи первую заметку в списке")
        context = self._context()
        with (
            patch("modules.router.open_note_selection", new=AsyncMock(return_value=True)) as select_note,
            patch("modules.router.handle_task_text", new=AsyncMock(return_value=True)) as task,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        select_note.assert_awaited_once_with(update, context, "Нет выведи первую заметку в списке")
        task.assert_not_awaited()


class NoteOrdinalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    USER_ID = 817263540

    def setUp(self):
        self.created_ids: list[int] = []

    def tearDown(self):
        for note_id in self.created_ids:
            delete_note(self.USER_ID, note_id)

    def _create(self, title: str, body: str):
        note = create_note(self.USER_ID, body, title=title)
        self.created_ids.append(note["note_id"])
        return note

    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    def _update(self, text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=self.USER_ID),
        )

    async def test_list_then_first_note_uses_exact_displayed_order(self):
        self._create("Альфа", "Первая тестовая заметка")
        self._create("Бета", "Вторая тестовая заметка")
        context = self._context()

        listing = self._update("Покажи мои заметки")
        self.assertTrue(await route_text(listing, context))
        self.assertIn(NOTE_LIST_CONTEXT_KEY, context.user_data)
        first_id = context.user_data[NOTE_LIST_CONTEXT_KEY]["note_ids"][0]
        expected = get_note(self.USER_ID, first_id)

        selection = self._update("Первая заметка")
        self.assertTrue(await route_text(selection, context))
        reply = selection.message.reply_text.await_args.args[0]
        self.assertIn(expected["title"], reply)
        self.assertIn(expected["text"], reply)
        self.assertEqual(context.user_data[ACTIVE_NOTE_KEY]["note_id"], first_id)

    async def test_all_three_screenshot_variants_open_note(self):
        note = self._create("Список покупок", "Бананы, масло, шоколад")
        for phrase in (
            "Первая заметка",
            "Нет выведи первую заметку в списке",
            "Покажи первую заметку",
        ):
            context = self._context()
            update = self._update(phrase)
            self.assertTrue(await route_text(update, context), phrase)
            reply = update.message.reply_text.await_args.args[0]
            self.assertIn("Список покупок", reply, phrase)
            self.assertIn("Бананы", reply, phrase)
            self.assertEqual(context.user_data[ACTIVE_NOTE_KEY]["note_id"], note["note_id"])

    async def test_out_of_range_number_is_explained(self):
        self._create("Одна", "Единственная заметка")
        context = self._context()
        update = self._update("Покажи вторую заметку")
        self.assertTrue(await route_text(update, context))
        self.assertIn("В списке только 1 заметок", update.message.reply_text.await_args.args[0])

    async def test_explicit_task_with_note_words_still_goes_to_tasks(self):
        self._create("Список покупок", "Вода")
        context = self._context()
        update = self._update("Добавь задачу открыть первую заметку")
        with patch("modules.router.handle_task_text", new=AsyncMock(return_value=True)) as task:
            self.assertTrue(await route_text(update, context))
        task.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
