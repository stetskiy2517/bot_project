from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from modules.note_reference import extract_note_reference, resolve_note_reference
from modules.notes import NOTE_SEARCH
from modules.router import route_text


class NoteReferenceParsingTests(unittest.TestCase):
    def test_generic_reference_can_use_any_note_name(self):
        self.assertEqual(
            extract_note_reference("что у меня по проекту Альфа"),
            ("проекту Альфа", False),
        )
        self.assertEqual(
            extract_note_reference("покажи идеи для ремонта кухни"),
            ("идеи для ремонта кухни", False),
        )
        self.assertEqual(
            extract_note_reference("что у меня про Иванова"),
            ("Иванова", False),
        )

    def test_explicit_storage_language_is_notes_even_for_event_words(self):
        self.assertEqual(
            extract_note_reference("что я записывал про встречу с Ивановым"),
            ("встречу с Ивановым", True),
        )
        self.assertEqual(
            extract_note_reference("что сохранено по ремонту кухни"),
            ("ремонту кухни", True),
        )

    def test_non_read_command_is_not_contextual_note_reference(self):
        self.assertIsNone(extract_note_reference("встреча с Ивановым завтра в 15"))
        self.assertIsNone(extract_note_reference("когда у меня встреча с Ивановым"))

    def test_generic_reference_requires_existing_user_note(self):
        with patch("modules.note_reference.search_notes", return_value=[{"note_id": 7}]) as search:
            query = resolve_note_reference(42, "что у меня по проекту Альфа")
        self.assertEqual(query, "проекту Альфа")
        search.assert_called_once_with(42, "проекту Альфа", limit=1)

        with patch("modules.note_reference.search_notes", return_value=[]):
            self.assertIsNone(resolve_note_reference(42, "что у меня по проекту Альфа"))

    def test_calendar_can_disable_only_generic_reference(self):
        with patch("modules.note_reference.search_notes") as search:
            self.assertIsNone(
                resolve_note_reference(42, "что у меня в пятницу", allow_generic=False)
            )
        search.assert_not_called()
        self.assertEqual(
            resolve_note_reference(
                42,
                "что я записывал про встречу с Ивановым",
                allow_generic=False,
            ),
            "встречу с Ивановым",
        )


class ContextualNoteRouterTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _update(text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text),
            effective_user=SimpleNamespace(id=42),
        )

    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    async def test_existing_named_note_wins_over_generic_calendar_view(self):
        update = self._update("что у меня по проекту Альфа")
        context = self._context()
        with (
            patch("modules.router.resolve_note_reference", return_value="проекту Альфа") as resolver,
            patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note_handler,
            patch("modules.router.view_from_text", new=AsyncMock(return_value=True)) as calendar_view,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        resolver.assert_called_once_with(42, update.message.text, allow_generic=True)
        self.assertEqual(note_handler.await_args.args[3], NOTE_SEARCH)
        self.assertIn("проекту Альфа", note_handler.await_args.args[2])
        calendar_view.assert_not_awaited()

    async def test_calendar_date_blocks_generic_note_lookup(self):
        update = self._update("что у меня в пятницу")
        context = self._context()
        with (
            patch("modules.router.resolve_note_reference", return_value=None) as resolver,
            patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note_handler,
            patch("modules.router.view_from_text", new=AsyncMock(return_value=True)) as calendar_view,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        resolver.assert_called_once_with(42, update.message.text, allow_generic=False)
        note_handler.assert_not_awaited()
        calendar_view.assert_awaited_once()

    async def test_explicit_note_language_can_contain_calendar_words(self):
        update = self._update("что я записывал про встречу с Ивановым")
        context = self._context()
        with (
            patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note_handler,
            patch("modules.router.view_from_text", new=AsyncMock(return_value=True)) as calendar_view,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        self.assertEqual(note_handler.await_args.args[3], NOTE_SEARCH)
        self.assertIn("встречу с Ивановым", note_handler.await_args.args[2])
        calendar_view.assert_not_awaited()

    async def test_unknown_generic_reference_falls_through_to_calendar(self):
        update = self._update("что у меня по проекту Альфа")
        context = self._context()
        with (
            patch("modules.router.resolve_note_reference", return_value=None),
            patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note_handler,
            patch("modules.router.view_from_text", new=AsyncMock(return_value=True)) as calendar_view,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        note_handler.assert_not_awaited()
        calendar_view.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
