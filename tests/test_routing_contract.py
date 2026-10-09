from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.conversation_context import remember_entity, set_pending
from modules.router import INTENT_UNKNOWN, detect_intent, route_text


class _Message:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **_kwargs):
        self.replies.append(text)


class _Context:
    def __init__(self):
        self.user_data = {}
        self.args = []


def _update(text: str, user_id: int = 123):
    return SimpleNamespace(
        message=_Message(text),
        effective_user=SimpleNamespace(id=user_id, full_name="Test User"),
    )


class RoutingPrecedenceContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_task_outranks_note_even_when_both_detectors_match(self):
        update = _update("Добавь в задачи купить воду")
        context = _Context()
        with (
            patch("modules.router.detect_task_intent", return_value="task_create"),
            patch("modules.router.detect_note_intent", return_value="note_append"),
            patch("modules.router.handle_task_text", new=AsyncMock(return_value=True)) as task,
            patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note,
        ):
            self.assertTrue(await route_text(update, context))
        task.assert_awaited_once()
        note.assert_not_awaited()

    async def test_reminder_outranks_task_and_note(self):
        update = _update("Напомни через 30 минут купить воду")
        context = _Context()
        with (
            patch("modules.router.detect_reminder_intent", return_value="reminder_create"),
            patch("modules.router.detect_task_intent", return_value="task_create") as task_detect,
            patch("modules.router.detect_note_intent", return_value="note_create") as note_detect,
            patch("modules.router.handle_reminder_text", new=AsyncMock(return_value=True)) as reminder,
        ):
            self.assertTrue(await route_text(update, context))
        reminder.assert_awaited_once()
        task_detect.assert_not_called()
        note_detect.assert_not_called()

    async def test_explicit_note_routes_to_note_not_calendar(self):
        update = _update("Создай заметку купить фильтр")
        context = _Context()
        with (
            patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar,
        ):
            self.assertTrue(await route_text(update, context))
        note.assert_awaited_once()
        calendar.assert_not_awaited()

    async def test_explicit_calendar_routes_to_calendar_not_note_or_task(self):
        update = _update("Добавь встречу завтра в 15:00")
        context = _Context()
        with (
            patch("modules.router.handle_note_text", new=AsyncMock(return_value=True)) as note,
            patch("modules.router.handle_task_text", new=AsyncMock(return_value=True)) as task,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar,
        ):
            self.assertTrue(await route_text(update, context))
        calendar.assert_awaited_once()
        note.assert_not_awaited()
        task.assert_not_awaited()

    async def test_active_note_short_followup_remains_note_context(self):
        update = _update("Добавь воду")
        context = _Context()
        remember_entity(context, "note", 41, "Список покупок")
        note = {"note_id": 41, "title": "Список покупок", "text": "хлеб"}
        with (
            patch("modules.router.resolve_named_note_append", return_value=None),
            patch("modules.router.get_active_note", return_value=note),
            patch("modules.router.append_to_note", new=AsyncMock(return_value=True)) as append,
            patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as calendar,
        ):
            self.assertTrue(await route_text(update, context))
        append.assert_awaited_once()
        calendar.assert_not_awaited()

    async def test_new_task_command_interrupts_stale_calendar_time_prompt(self):
        update = _update("Создай задачу купить молоко")
        context = _Context()
        set_pending(context, {"type": "create_time", "text": "Встреча"})
        with (
            patch("modules.router._resume_pending", new=AsyncMock(return_value=True)) as resume,
            patch("modules.router.handle_task_text", new=AsyncMock(return_value=True)) as task,
        ):
            self.assertTrue(await route_text(update, context))
        resume.assert_not_awaited()
        task.assert_awaited_once()

    def test_plain_conversation_is_not_forced_into_calendar(self):
        for text in (
            "Почему я всё откладываю?",
            "Как лучше подготовиться к встрече?",
            "Я люблю гулять вечером",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
