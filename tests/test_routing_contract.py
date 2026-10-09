from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.conversation_context import remember_entity, set_pending
from modules.router import INTENT_UNKNOWN, detect_intent, handle_text, route_text


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


class TelegramFallbackPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_deterministic_success_does_not_call_ai(self):
        update = _update("Создай задачу купить воду")
        context = _Context()
        with (
            patch("modules.router.route_text", new=AsyncMock(return_value=True)) as route,
            patch("modules.ai_task_planner.handle_unhandled_task_plan", new=AsyncMock()) as planner,
            patch("modules.ai_assistant.interpret_unhandled_action") as rewrite,
            patch("modules.ai_assistant.answer_unhandled") as chat,
        ):
            await handle_text(update, context)
        route.assert_awaited_once()
        planner.assert_not_awaited()
        rewrite.assert_not_called()
        chat.assert_not_called()

    async def test_task_list_ai_precedes_action_rewrite_and_chat(self):
        update = _update("отправить паспорт, позвонить Ольхову, сделать домашку")
        context = _Context()
        with (
            patch("modules.router.route_text", new=AsyncMock(return_value=False)),
            patch(
                "modules.ai_task_planner.handle_unhandled_task_plan",
                new=AsyncMock(return_value=True),
            ) as planner,
            patch("modules.ai_assistant.interpret_unhandled_action") as rewrite,
            patch("modules.ai_assistant.answer_unhandled") as chat,
        ):
            await handle_text(update, context)
        planner.assert_awaited_once()
        rewrite.assert_not_called()
        chat.assert_not_called()

    async def test_ai_action_rewrite_is_executed_only_through_router(self):
        update = _update("Закинь в дела купить воду сегодня")
        context = _Context()
        route = AsyncMock(side_effect=[False, True])
        with (
            patch("modules.router.route_text", new=route),
            patch(
                "modules.ai_task_planner.handle_unhandled_task_plan",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "modules.ai_assistant.interpret_unhandled_action",
                return_value="добавь задачу купить воду сегодня",
            ),
            patch("modules.ai_assistant.answer_unhandled") as chat,
        ):
            await handle_text(update, context)
        self.assertEqual(route.await_count, 2)
        self.assertEqual(route.await_args_list[1].kwargs["text"], "добавь задачу купить воду сегодня")
        chat.assert_not_called()

    async def test_plain_chat_reaches_conversational_ai_last(self):
        update = _update("Почему я всё откладываю?")
        context = _Context()
        with (
            patch("modules.router.route_text", new=AsyncMock(return_value=False)),
            patch(
                "modules.ai_task_planner.handle_unhandled_task_plan",
                new=AsyncMock(return_value=False),
            ),
            patch("modules.ai_assistant.interpret_unhandled_action", return_value=None),
            patch("modules.ai_assistant.answer_unhandled", return_value="Давай разберём причины.") as chat,
            patch("core.chat_context.recent_chat_messages", return_value=[]),
            patch("core.chat_context.append_chat_exchange") as append,
        ):
            await handle_text(update, context)
        chat.assert_called_once()
        append.assert_called_once()
        self.assertEqual(update.message.replies, ["Давай разберём причины."])


if __name__ == "__main__":
    unittest.main()
