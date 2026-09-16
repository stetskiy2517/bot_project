from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from modules.reminders import (
    REMINDER_UPDATE,
    _split_update_request,
    detect_reminder_intent,
    update_reminder_from_text,
)
from modules.router import route_text


class ReminderUpdateParsingTests(unittest.TestCase):
    def test_update_intent_is_detected(self):
        self.assertEqual(
            detect_reminder_intent("перенеси напоминание про таблетки на завтра в 9"),
            REMINDER_UPDATE,
        )
        self.assertEqual(
            detect_reminder_intent("измени напоминание про таблетки категория здоровье"),
            REMINDER_UPDATE,
        )

    def test_update_command_splits_target_and_change(self):
        self.assertEqual(
            _split_update_request("перенеси напоминание про таблетки на завтра в 9"),
            ("про таблетки", "на завтра в 9"),
        )
        self.assertEqual(
            _split_update_request("измени напоминание про таблетки категория здоровье"),
            ("про таблетки", "категория здоровье"),
        )
        self.assertEqual(
            _split_update_request("измени это напоминание категория личное"),
            ("", "категория личное"),
        )


class ReminderUpdateRouterTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    async def test_router_does_not_send_reminder_update_to_calendar(self):
        text = "перенеси напоминание про таблетки на завтра в 9"
        update = SimpleNamespace(
            message=SimpleNamespace(text=text),
            effective_user=SimpleNamespace(id=1),
        )
        context = self._context()
        with (
            patch("modules.router.handle_reminder_text", new=AsyncMock(return_value=True)) as reminder_handler,
            patch("modules.router.update_from_text", new=AsyncMock(return_value=True)) as calendar_update,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        reminder_handler.assert_awaited_once_with(update, context, text, REMINDER_UPDATE)
        calendar_update.assert_not_awaited()

    async def test_category_edit_updates_matching_reminder(self):
        text = "измени напоминание про таблетки категория здоровье"
        update = SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=1),
        )
        context = self._context()
        reminder = {
            "reminder_id": 7,
            "user_id": 1,
            "text": "Принять таблетки",
            "remind_at": "2026-09-17T18:00:00+00:00",
            "repeat_rule": None,
        }
        edited = dict(reminder)
        with (
            patch("modules.reminders.get_user_timezone", return_value="Europe/Moscow"),
            patch("modules.reminders.list_active_reminders", return_value=[reminder]),
            patch("modules.reminders.edit_saved_reminder", return_value=edited) as edit,
            patch("modules.reminders.reminder_category", return_value="health"),
        ):
            handled = await update_reminder_from_text(update, context, text)
        self.assertTrue(handled)
        edit.assert_called_once_with(1, 7, category="health")
        update.message.reply_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
