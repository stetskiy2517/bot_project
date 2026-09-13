from __future__ import annotations

from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, patch

from modules.note_conversation import (
    ACTIVE_NOTE_KEY,
    NoteAppendResolution,
    active_note_addition,
    append_to_note,
    get_active_note,
    remember_active_note,
    resolve_named_note_append,
)
from modules.router import route_text


NOTE = {
    "note_id": 41,
    "user_id": 1,
    "title": "Список покупок",
    "text": "Бананы\nмасло сливочное",
    "created_at": "2026-09-13T09:00:00+00:00",
    "updated_at": "2026-09-13T09:00:00+00:00",
}


class NoteConversationParsingTests(unittest.TestCase):
    def test_target_first_word_order(self):
        with patch("modules.note_conversation.search_notes", return_value=[NOTE]):
            resolved = resolve_named_note_append(1, "Добавь в список покупок шоколад")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.query, "список покупок")
        self.assertEqual(resolved.addition, "шоколад")
        self.assertEqual(resolved.matches[0]["note_id"], 41)

    def test_reverse_word_order_from_screenshot(self):
        def search(_user_id, query, *, limit):
            return [NOTE] if query.lower() == "список покупок" else []

        with patch("modules.note_conversation.search_notes", side_effect=search):
            resolved = resolve_named_note_append(1, "Добавь воду в список покупок")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.query, "список покупок")
        self.assertEqual(resolved.addition, "воду")

    def test_reverse_word_order_accepts_possessive_and_note_filler(self):
        with patch("modules.note_conversation.search_notes", return_value=[NOTE]):
            resolved = resolve_named_note_append(1, "Добавь воду в мою заметку Список покупок")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.query, "Список покупок")
        self.assertEqual(resolved.addition, "воду")

    def test_named_append_does_not_claim_missing_note(self):
        with patch("modules.note_conversation.search_notes", return_value=[]):
            self.assertIsNone(resolve_named_note_append(1, "Добавь воду в несуществующую заметку"))

    def test_short_active_followup(self):
        self.assertEqual(active_note_addition("Добавь воду"), "воду")
        self.assertEqual(active_note_addition("добавь ещё молоко"), "молоко")
        self.assertIsNone(active_note_addition("добавь встречу"))
        self.assertIsNone(active_note_addition("добавь воду в список покупок"))


class ActiveNoteStateTests(unittest.TestCase):
    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    def test_remember_and_restore_active_note(self):
        context = self._context()
        remember_active_note(context, NOTE)
        self.assertIn(ACTIVE_NOTE_KEY, context.user_data)
        with patch("modules.note_conversation.get_note", return_value=NOTE):
            current = get_active_note(context, 1)
        self.assertEqual(current["note_id"], 41)

    def test_expired_active_note_is_not_used(self):
        context = self._context()
        context.user_data[ACTIVE_NOTE_KEY] = {
            "note_id": 41,
            "title": "Список покупок",
            "touched_at": time.time() - 3600,
        }
        with patch("modules.note_conversation.get_note") as get_note:
            current = get_active_note(context, 1)
        self.assertIsNone(current)
        get_note.assert_not_called()
        self.assertNotIn(ACTIVE_NOTE_KEY, context.user_data)


class DirectAppendReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_reply_does_not_echo_whole_note(self):
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace(user_data={})
        updated = {**NOTE, "text": NOTE["text"] + "\nшоколад"}
        with patch("modules.note_conversation.append_note", return_value=updated):
            handled = await append_to_note(update, context, NOTE, "шоколад")
        self.assertTrue(handled)
        reply = update.message.reply_text.await_args.args[0]
        self.assertEqual(reply, "Добавил в «Список покупок»: шоколад.")
        self.assertNotIn("масло сливочное", reply)
        self.assertEqual(context.user_data[ACTIVE_NOTE_KEY]["note_id"], 41)


class NoteConversationRouterTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _update(text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=1),
        )

    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    async def test_target_first_append_is_direct_and_not_calendar(self):
        update = self._update("Добавь в список покупок шоколад")
        context = self._context()
        resolution = NoteAppendResolution("список покупок", "шоколад", [NOTE])
        with (
            patch("modules.router.resolve_named_note_append", return_value=resolution),
            patch("modules.router.append_to_note", new=AsyncMock(return_value=True)) as append,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        append.assert_awaited_once_with(update, context, NOTE, "шоколад")
        calendar.assert_not_awaited()

    async def test_reverse_append_is_direct_and_not_calendar(self):
        update = self._update("Добавь воду в список покупок")
        context = self._context()
        resolution = NoteAppendResolution("список покупок", "воду", [NOTE])
        with (
            patch("modules.router.resolve_named_note_append", return_value=resolution),
            patch("modules.router.append_to_note", new=AsyncMock(return_value=True)) as append,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        append.assert_awaited_once_with(update, context, NOTE, "воду")
        calendar.assert_not_awaited()

    async def test_short_followup_uses_recent_note_context(self):
        update = self._update("Добавь воду")
        context = self._context()
        with (
            patch("modules.router.resolve_named_note_append", return_value=None),
            patch("modules.router.get_active_note", return_value=NOTE),
            patch("modules.router.append_to_note", new=AsyncMock(return_value=True)) as append,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        append.assert_awaited_once_with(update, context, NOTE, "воду")
        calendar.assert_not_awaited()

    async def test_calendar_command_is_not_captured_by_active_note(self):
        update = self._update("Добавь встречу завтра в 15:00")
        context = self._context()
        with (
            patch("modules.router.resolve_named_note_append", return_value=None),
            patch("modules.router.get_active_note", return_value=NOTE),
            patch("modules.router.append_to_note", new=AsyncMock(return_value=True)) as append,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        append.assert_not_awaited()
        calendar.assert_awaited_once()

    async def test_reminder_command_keeps_priority_over_note_context(self):
        update = self._update("Напомни через 30 минут купить воду")
        context = self._context()
        with (
            patch("modules.router.detect_reminder_intent", return_value="reminder_create"),
            patch("modules.router.handle_reminder_text", new=AsyncMock(return_value=True)) as reminder,
            patch("modules.router.append_to_note", new=AsyncMock(return_value=True)) as append,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        reminder.assert_awaited_once()
        append.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
