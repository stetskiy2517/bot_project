from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from modules.notes import (
    NOTE_APPEND,
    NOTE_CREATE,
    NOTE_DELETE,
    NOTE_LIST,
    NOTE_SEARCH,
    _note_body,
    _split_append_payload,
    append_note_from_text,
    detect_note_intent,
)
from modules.router import route_text


class NoteParsingTests(unittest.TestCase):
    def test_create_intents(self):
        self.assertEqual(detect_note_intent("запиши заметку купить фильтр для воды"), NOTE_CREATE)
        self.assertEqual(detect_note_intent("заметка: Иван ждёт смету"), NOTE_CREATE)
        self.assertEqual(_note_body("запиши заметку купить фильтр для воды"), "купить фильтр для воды")
        self.assertEqual(_note_body("заметка: Иван ждёт смету"), "Иван ждёт смету")

    def test_list_style_commands_are_notes(self):
        phrase = "запиши список продуктов бананы масло сливочное"
        self.assertEqual(detect_note_intent(phrase), NOTE_CREATE)
        self.assertEqual(_note_body(phrase), "список продуктов бананы масло сливочное")
        self.assertEqual(detect_note_intent("создай список вещей в поездку"), NOTE_CREATE)
        self.assertEqual(detect_note_intent("составь список покупок молоко хлеб"), NOTE_CREATE)

    def test_append_intents_and_payload(self):
        command = "добавь в заметку про Иванова, что он согласовал цену"
        self.assertEqual(detect_note_intent(command), NOTE_APPEND)
        self.assertEqual(
            _split_append_payload(command),
            ("Иванова", "он согласовал цену", True),
        )
        command = "допиши в заметку про фильтр — купить картридж"
        self.assertEqual(detect_note_intent(command), NOTE_APPEND)
        self.assertEqual(
            _split_append_payload(command),
            ("фильтр", "купить картридж", True),
        )

    def test_append_without_preposition_is_not_new_note(self):
        self.assertEqual(
            detect_note_intent("дополни заметку про отпуск: забронировать машину"),
            NOTE_APPEND,
        )

    def test_list_search_delete_intents(self):
        self.assertEqual(detect_note_intent("покажи мои заметки"), NOTE_LIST)
        self.assertEqual(detect_note_intent("найди заметки про Иванова"), NOTE_SEARCH)
        self.assertEqual(detect_note_intent("удали заметку про фильтр"), NOTE_DELETE)

    def test_plain_calendar_command_is_not_note(self):
        self.assertIsNone(detect_note_intent("запиши врача завтра в 19"))
        self.assertIsNone(detect_note_intent("удали встречу завтра"))


class NoteAppendTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _update(text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=1),
        )

    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    async def test_append_to_single_matching_note(self):
        update = self._update("добавь в заметку про Иванова, что он согласовал цену")
        context = self._context()
        note = {
            "note_id": 7,
            "text": "Иван ждёт смету",
            "created_at": "2026-09-13T09:00:00+00:00",
            "updated_at": "2026-09-13T09:00:00+00:00",
        }
        updated = {**note, "text": "Иван ждёт смету\nон согласовал цену"}
        with (
            patch("modules.notes.search_notes", return_value=[note]) as search,
            patch("modules.notes.append_note", return_value=updated) as append,
        ):
            handled = await append_note_from_text(update, context, update.message.text)
        self.assertTrue(handled)
        search.assert_called_once_with(1, "Иванова", limit=50)
        append.assert_called_once_with(1, 7, "он согласовал цену")
        self.assertNotIn("smart_planner_pending", context.user_data)

    async def test_append_asks_which_note_when_several_match(self):
        update = self._update("добавь в заметку про Иванова, что созвон в пятницу")
        context = self._context()
        notes = [
            {"note_id": 1, "text": "Иванов проект А", "created_at": "2026-09-13T09:00:00+00:00", "updated_at": "2026-09-13T09:00:00+00:00"},
            {"note_id": 2, "text": "Иванов проект Б", "created_at": "2026-09-13T08:00:00+00:00", "updated_at": "2026-09-13T08:00:00+00:00"},
        ]
        with (
            patch("modules.notes.search_notes", return_value=notes),
            patch("modules.notes.append_note") as append,
        ):
            handled = await append_note_from_text(update, context, update.message.text)
        self.assertTrue(handled)
        append.assert_not_called()
        pending = context.user_data["smart_planner_pending"]
        self.assertEqual(pending["type"], "note_select_append")
        self.assertEqual(pending["addition"], "созвон в пятницу")

    async def test_append_asks_for_text_when_only_target_was_given(self):
        update = self._update("добавь в заметку про Иванова")
        context = self._context()
        note = {
            "note_id": 7,
            "text": "Иван ждёт смету",
            "created_at": "2026-09-13T09:00:00+00:00",
            "updated_at": "2026-09-13T09:00:00+00:00",
        }
        with patch("modules.notes.search_notes", return_value=[note]):
            handled = await append_note_from_text(update, context, update.message.text)
        self.assertTrue(handled)
        pending = context.user_data["smart_planner_pending"]
        self.assertEqual(pending["type"], "note_append_text")
        self.assertEqual(pending["note"]["note_id"], 7)


class NoteRouterTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _update(text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text),
            effective_user=SimpleNamespace(id=1),
        )

    @staticmethod
    def _context(pending: dict | None = None):
        return SimpleNamespace(user_data={"smart_planner_pending": pending} if pending else {})

    async def test_router_sends_note_command_to_note_module_not_calendar(self):
        update = self._update("запиши заметку Иван ждёт смету")
        context = self._context()
        with (
            patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note_handler,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar_create,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        note_handler.assert_awaited_once()
        calendar_create.assert_not_awaited()

    async def test_router_sends_spoken_list_to_notes_not_calendar(self):
        update = self._update("запиши список продуктов бананы масло сливочное")
        context = self._context()
        with (
            patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note_handler,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar_create,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        self.assertEqual(note_handler.await_args.args[3], NOTE_CREATE)
        calendar_create.assert_not_awaited()

    async def test_router_sends_append_command_to_notes_not_calendar(self):
        update = self._update("добавь в заметку про Иванова, что созвон в пятницу")
        context = self._context()
        with (
            patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note_handler,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar_create,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        self.assertEqual(note_handler.await_args.args[3], NOTE_APPEND)
        calendar_create.assert_not_awaited()

    async def test_pending_note_is_resumed_by_router(self):
        update = self._update("Иван ждёт смету")
        context = self._context({"type": "note_text"})
        with patch("modules.router.resume_pending_note", new=AsyncMock(return_value=True)) as resume:
            handled = await route_text(update, context)
        self.assertTrue(handled)
        resume.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
