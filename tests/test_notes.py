from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from modules.notes import (
    NOTE_CREATE,
    NOTE_DELETE,
    NOTE_LIST,
    NOTE_SEARCH,
    _note_body,
    detect_note_intent,
)
from modules.router import route_text


class NoteParsingTests(unittest.TestCase):
    def test_create_intents(self):
        self.assertEqual(detect_note_intent("запиши заметку купить фильтр для воды"), NOTE_CREATE)
        self.assertEqual(detect_note_intent("заметка: Иван ждёт смету"), NOTE_CREATE)
        self.assertEqual(_note_body("запиши заметку купить фильтр для воды"), "купить фильтр для воды")
        self.assertEqual(_note_body("заметка: Иван ждёт смету"), "Иван ждёт смету")

    def test_list_search_delete_intents(self):
        self.assertEqual(detect_note_intent("покажи мои заметки"), NOTE_LIST)
        self.assertEqual(detect_note_intent("найди заметки про Иванова"), NOTE_SEARCH)
        self.assertEqual(detect_note_intent("удали заметку про фильтр"), NOTE_DELETE)

    def test_plain_calendar_command_is_not_note(self):
        self.assertIsNone(detect_note_intent("запиши врача завтра в 19"))
        self.assertIsNone(detect_note_intent("удали встречу завтра"))


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

    async def test_pending_note_is_resumed_by_router(self):
        update = self._update("Иван ждёт смету")
        context = self._context({"type": "note_text"})
        with patch("modules.router.resume_pending_note", new=AsyncMock(return_value=True)) as resume:
            handled = await route_text(update, context)
        self.assertTrue(handled)
        resume.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
