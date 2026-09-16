import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from modules.router import (
    INTENT_CREATE,
    INTENT_UNKNOWN,
    _sync_active_reminder_reference,
    detect_intent,
    route_text,
)


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
    def test_first_person_medication_fact_with_date_and_time_is_not_calendar_write(self):
        self.assertEqual(
            detect_intent("Я принимаю таблетки завтра в 23:00").name,
            INTENT_UNKNOWN,
        )

    def test_explicit_and_natural_calendar_writes_still_work(self):
        for text in (
            "Добавь встречу завтра в 15:00",
            "Встреча завтра в 15:00",
            "Я иду к врачу завтра в 15:00",
            "Мне завтра в 9 к врачу",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_CREATE)

    def test_opened_library_reminder_becomes_one_shot_current_reference(self):
        context = _Context({
            "smart_planner_last_reminder": {"reminder_id": 4, "text": "старое"},
            "smart_planner_active_reminder": {"reminder_id": 9, "text": "таблетка"},
        })
        _sync_active_reminder_reference(context)
        self.assertEqual(
            context.user_data["smart_planner_last_reminder"],
            {"reminder_id": 9, "text": "таблетка"},
        )
        self.assertNotIn("smart_planner_active_reminder", context.user_data)


class RouterPendingInterruptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_new_command_interrupts_confirmation(self):
        update = SimpleNamespace(
            message=_Message("Покажи календарь на завтра"),
            effective_user=SimpleNamespace(id=123),
        )
        context = _Context({"smart_planner_pending": {"type": "confirm_delete"}})

        with (
            patch("modules.router.handle_template", new=AsyncMock(return_value=False)),
            patch("modules.router._resume_pending", new=AsyncMock(return_value=True)) as resume,
            patch("modules.router.view_from_text", new=AsyncMock(return_value=True)) as view,
        ):
            handled = await route_text(update, context)

        self.assertTrue(handled)
        resume.assert_not_awaited()
        view.assert_awaited_once()
        self.assertNotIn("smart_planner_pending", context.user_data)

    async def test_time_reply_stays_inside_time_prompt(self):
        update = SimpleNamespace(
            message=_Message("завтра в 15"),
            effective_user=SimpleNamespace(id=123),
        )
        context = _Context({"smart_planner_pending": {"type": "create_time", "text": "Встреча"}})

        with (
            patch("modules.router.handle_template", new=AsyncMock(return_value=False)),
            patch("modules.router._resume_pending", new=AsyncMock(return_value=True)) as resume,
        ):
            handled = await route_text(update, context)

        self.assertTrue(handled)
        resume.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
