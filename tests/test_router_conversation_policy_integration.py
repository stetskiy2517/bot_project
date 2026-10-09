import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.conversation_context import current_entity, get_pending
from modules.router import INTENT_CREATE, INTENT_UNKNOWN, detect_intent, route_text


class _Message:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **_kwargs):
        self.replies.append(text)


class _Context:
    def __init__(self, user_data=None):
        self.user_data = dict(user_data or {})
        self.args = []


class RouterConversationIntentTests(unittest.TestCase):
    def test_first_person_medication_facts_are_not_calendar_writes(self):
        for text in (
            "Я принимаю таблетки завтра в 23:00",
            "Я завтра в 23:00 принимаю таблетки",
            "Я каждый вечер в 22 пью таблетки",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_UNKNOWN)

    def test_explicit_and_natural_calendar_writes_still_work(self):
        for text in (
            "Добавь встречу завтра в 15:00",
            "Встреча завтра в 15:00",
            "Я иду к врачу завтра в 15:00",
            "Мне завтра в 9 к врачу",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_CREATE)

    def test_legacy_opened_reminder_migrates_into_unified_context(self):
        context = _Context({
            "smart_planner_active_reminder": {"reminder_id": 9, "text": "таблетка"},
        })
        reference = current_entity(context, "reminder")
        self.assertEqual(reference["id"], "9")
        self.assertEqual(reference["title"], "таблетка")
        self.assertNotIn("smart_planner_active_reminder", context.user_data)


class RouterPendingInterruptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_new_command_interrupts_confirmation(self):
        update = SimpleNamespace(
            message=_Message("Покажи календарь на завтра"),
            effective_user=SimpleNamespace(id=123),
        )
        context = _Context({"smart_planner_context": {"version": 1, "current_entity": None, "recent_entities": [], "pending": {"type": "confirm_delete"}}})

        with (
            patch("modules.router.handle_template", new=AsyncMock(return_value=False)),
            patch("modules.router._resume_pending", new=AsyncMock(return_value=True)) as resume,
            patch("modules.router.view_from_text", new=AsyncMock(return_value=True)) as view,
        ):
            handled = await route_text(update, context)

        self.assertTrue(handled)
        resume.assert_not_awaited()
        view.assert_awaited_once()
        self.assertIsNone(get_pending(context))

    async def test_free_time_query_interrupts_confirmation(self):
        update = SimpleNamespace(
            message=_Message("Когда завтра свободно?"),
            effective_user=SimpleNamespace(id=123),
        )
        context = _Context({"smart_planner_pending": {"type": "confirm_delete"}})

        with (
            patch("modules.router.handle_template", new=AsyncMock(return_value=False)),
            patch("modules.router._resume_pending", new=AsyncMock(return_value=True)) as resume,
            patch("modules.router.free_slots_from_text", new=AsyncMock(return_value=True)) as free_slots,
        ):
            handled = await route_text(update, context)

        self.assertTrue(handled)
        resume.assert_not_awaited()
        free_slots.assert_awaited_once_with(update, context, "Когда завтра свободно?")
        self.assertNotIn("smart_planner_pending", context.user_data)

    async def test_entity_correction_reuses_pending_payload_as_task(self):
        update = SimpleNamespace(
            message=_Message("Не событие, задачу сделай"),
            effective_user=SimpleNamespace(id=123),
        )
        context = _Context({
            "smart_planner_context": {
                "version": 1,
                "current_entity": None,
                "recent_entities": [],
                "pending": {"type": "create_time", "text": "постирать белье на сегодня"},
            }
        })

        with patch(
            "modules.router.handle_task_text",
            new=AsyncMock(return_value=True),
        ) as task:
            handled = await route_text(update, context)

        self.assertTrue(handled)
        task.assert_awaited_once()
        self.assertEqual(
            task.await_args.args[2],
            "создай задачу постирать белье на сегодня",
        )
        self.assertIsNone(get_pending(context))

    async def test_reminder_time_can_be_retargeted_to_task_without_losing_payload(self):
        update = SimpleNamespace(
            message=_Message("Не напоминание, а задача"),
            effective_user=SimpleNamespace(id=123),
        )
        context = _Context({
            "smart_planner_context": {
                "version": 1,
                "current_entity": None,
                "recent_entities": [],
                "pending": {
                    "type": "reminder_time",
                    "text": "напомни купить молоко",
                    "title": "Купить молоко",
                    "timezone": "Europe/Moscow",
                },
            }
        })

        with patch(
            "modules.router.handle_task_text",
            new=AsyncMock(return_value=True),
        ) as task:
            handled = await route_text(update, context)

        self.assertTrue(handled)
        task.assert_awaited_once()
        self.assertEqual(task.await_args.args[2], "создай задачу купить молоко")
        self.assertIsNone(get_pending(context))

    async def test_plain_new_task_command_does_not_reuse_stale_pending_payload(self):
        update = SimpleNamespace(
            message=_Message("Создай задачу купить молоко"),
            effective_user=SimpleNamespace(id=123),
        )
        context = _Context({
            "smart_planner_context": {
                "version": 1,
                "current_entity": None,
                "recent_entities": [],
                "pending": {"type": "create_time", "text": "старая встреча"},
            }
        })

        with patch(
            "modules.router.handle_task_text",
            new=AsyncMock(return_value=True),
        ) as task:
            handled = await route_text(update, context)

        self.assertTrue(handled)
        task.assert_awaited_once()
        self.assertEqual(task.await_args.args[2], "Создай задачу купить молоко")
        self.assertIsNone(get_pending(context))

    async def test_time_reply_stays_inside_time_prompt(self):
        update = SimpleNamespace(
            message=_Message("завтра в 15"),
            effective_user=SimpleNamespace(id=123),
        )
        context = _Context({"smart_planner_context": {"version": 1, "current_entity": None, "recent_entities": [], "pending": {"type": "create_time", "text": "Встреча"}}})

        with (
            patch("modules.router.handle_template", new=AsyncMock(return_value=False)),
            patch("modules.router._resume_pending", new=AsyncMock(return_value=True)) as resume,
        ):
            handled = await route_text(update, context)

        self.assertTrue(handled)
        resume.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
