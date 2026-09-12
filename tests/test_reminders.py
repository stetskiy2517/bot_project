from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from modules.reminders import (
    REMINDER_CREATE,
    REMINDER_DELETE,
    REMINDER_LIST,
    _reminder_due_at,
    _reminder_title,
    detect_reminder_intent,
)
from modules.router import route_text


class ReminderParsingTests(unittest.TestCase):
    def test_remind_phrase_is_standalone_reminder(self):
        self.assertEqual(
            detect_reminder_intent("Напомню, в 10 вечера выпить таблетку."),
            REMINDER_CREATE,
        )
        self.assertEqual(
            _reminder_title("Напомню, в 10 вечера выпить таблетку."),
            "Выпить таблетку",
        )
        due = _reminder_due_at(
            "Напомню, в 10 вечера выпить таблетку.",
            "Europe/Moscow",
            datetime(2026, 9, 12, 21, 49),
        )
        self.assertIsNotNone(due)
        self.assertEqual((due.year, due.month, due.day, due.hour, due.minute), (2026, 9, 12, 22, 0))

    def test_relative_reminder(self):
        due = _reminder_due_at(
            "напомни через 30 минут позвонить маме",
            "Europe/Moscow",
            datetime(2026, 9, 12, 21, 0),
        )
        self.assertEqual((due.year, due.month, due.day, due.hour, due.minute), (2026, 9, 12, 21, 30))
        self.assertEqual(_reminder_title("напомни через 30 минут позвонить маме"), "Позвонить маме")

    def test_list_and_delete_intents(self):
        self.assertEqual(detect_reminder_intent("покажи мои напоминания"), REMINDER_LIST)
        self.assertEqual(detect_reminder_intent("удали напоминание про таблетки"), REMINDER_DELETE)

    def test_calendar_event_reminder_property_is_not_stolen(self):
        self.assertIsNone(detect_reminder_intent("удали напоминание у встречи завтра"))


class ReminderRouterTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _update(text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text),
            effective_user=SimpleNamespace(id=1),
        )

    @staticmethod
    def _context(pending: dict | None = None):
        return SimpleNamespace(user_data={"smart_planner_pending": pending} if pending else {})

    async def test_router_sends_remind_command_to_reminder_module_not_calendar(self):
        update = self._update("Напомню, в 10 вечера выпить таблетку.")
        context = self._context()
        with (
            patch("modules.router.handle_reminder_text", new=AsyncMock(return_value=True)) as reminder_handler,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar_create,
        ):
            handled = await route_text(update, context)
        self.assertTrue(handled)
        reminder_handler.assert_awaited_once()
        calendar_create.assert_not_awaited()

    async def test_conflict_can_be_forced_with_natural_phrase(self):
        update = self._update("Создай всё равно.")
        context = self._context({"type": "confirm_create_conflict", "event": {}, "alternatives": []})
        with patch("modules.router.resume_pending_action", new=AsyncMock(return_value=True)) as resume:
            handled = await route_text(update, context)
        self.assertTrue(handled)
        self.assertEqual(resume.await_args.args[2], "да")


if __name__ == "__main__":
    unittest.main()
