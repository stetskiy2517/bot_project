from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.conversation_context import get_pending, set_pending
from modules.ai_task_planner import (
    execute_task_plan,
    handle_unhandled_task_plan,
    interpret_task_plan,
    resume_pending_task_plan,
)


class AITaskPlannerInterpretationTests(unittest.TestCase):
    def test_list_estimates_keep_quick_digital_action_at_five_minutes(self):
        payload = {
            "intent": "task_list",
            "schedule": False,
            "day": "",
            "items": [
                {
                    "title": "отправить паспорт Артему",
                    "estimate_minutes": 5,
                    "category": "personal",
                    "priority": "normal",
                    "source_text": "отправить паспорт Артему",
                },
                {
                    "title": "позвонить Ольхову",
                    "estimate_minutes": 15,
                    "category": "work",
                    "priority": "normal",
                    "source_text": "позвонить Ольхову",
                },
                {
                    "title": "сделать домашнее задание",
                    "estimate_minutes": 60,
                    "category": "personal",
                    "priority": "normal",
                    "source_text": "сделать домашнее задание",
                },
            ],
        }
        with patch("modules.ai_task_planner.has_ai_access", return_value=True), \
             patch("modules.ai_task_planner.is_ai_available", return_value=True), \
             patch("modules.ai_task_planner.get_user_timezone", return_value="Europe/Moscow"), \
             patch("modules.ai_task_planner.complete_structured", return_value=payload):
            result = interpret_task_plan(
                "Отправить паспорт Артему, позвонить Ольхову, сделать домашнее задание",
                user_id=42,
                now=datetime(2026, 10, 7, 5, 48, tzinfo=timezone.utc),
            )

        self.assertIsNotNone(result)
        self.assertEqual([item["estimate_minutes"] for item in result["items"]], [5, 15, 60])
        self.assertEqual(result["items"][0]["title"], "Отправить паспорт Артему")

    def test_hallucinated_dacha_is_rejected(self):
        payload = {
            "intent": "task_list",
            "schedule": True,
            "day": "2026-10-15",
            "items": [
                {
                    "title": "Ремонт шкафа",
                    "estimate_minutes": 60,
                    "category": "personal",
                    "priority": "normal",
                    "source_text": "починим шкаф",
                },
                {
                    "title": "Поездка на дачу",
                    "estimate_minutes": 120,
                    "category": "travel",
                    "priority": "normal",
                    "source_text": "выходные",
                },
            ],
        }
        with patch("modules.ai_task_planner.has_ai_access", return_value=True), \
             patch("modules.ai_task_planner.is_ai_available", return_value=True), \
             patch("modules.ai_task_planner.get_user_timezone", return_value="Europe/Moscow"), \
             patch("modules.ai_task_planner.complete_structured", return_value=payload):
            result = interpret_task_plan(
                "Запланирую выходные, починим шкаф.",
                user_id=42,
                now=datetime(2026, 10, 9, 9, 29, tzinfo=timezone.utc),
            )

        self.assertIsNone(result)

    def test_history_is_not_sent_without_explicit_reference(self):
        payload = {
            "intent": "task_list",
            "schedule": False,
            "day": "",
            "items": [
                {
                    "title": "Позвонить Ольхову",
                    "estimate_minutes": 15,
                    "category": "work",
                    "priority": "normal",
                    "source_text": "позвонить Ольхову",
                },
                {
                    "title": "Убрать ванную",
                    "estimate_minutes": 30,
                    "category": "personal",
                    "priority": "normal",
                    "source_text": "убрать ванную",
                },
            ],
        }
        history = [
            {"role": "user", "content": "Поездка на дачу"},
            {"role": "assistant", "content": "Запомнил"},
        ]
        with patch("modules.ai_task_planner.has_ai_access", return_value=True), \
             patch("modules.ai_task_planner.is_ai_available", return_value=True), \
             patch("modules.ai_task_planner.get_user_timezone", return_value="Europe/Moscow"), \
             patch("modules.ai_task_planner.complete_structured", return_value=payload) as complete:
            result = interpret_task_plan(
                "позвонить Ольхову, убрать ванную",
                user_id=42,
                history=history,
                now=datetime(2026, 10, 9, 9, 29, tzinfo=timezone.utc),
            )

        self.assertIsNotNone(result)
        messages = complete.call_args.args[0]
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertNotIn("дачу", str(messages).casefold())

    def test_model_cannot_invent_day_without_date_in_user_text(self):
        payload = {
            "intent": "task_list",
            "schedule": True,
            "day": "2026-10-15",
            "items": [
                {
                    "title": "Починить шкаф",
                    "estimate_minutes": 60,
                    "category": "personal",
                    "priority": "normal",
                    "source_text": "починить шкаф",
                },
                {
                    "title": "Убрать ванную",
                    "estimate_minutes": 30,
                    "category": "personal",
                    "priority": "normal",
                    "source_text": "убрать ванную",
                },
            ],
        }
        with patch("modules.ai_task_planner.has_ai_access", return_value=True), \
             patch("modules.ai_task_planner.is_ai_available", return_value=True), \
             patch("modules.ai_task_planner.get_user_timezone", return_value="Europe/Moscow"), \
             patch("modules.ai_task_planner.complete_structured", return_value=payload):
            result = interpret_task_plan(
                "распланируй: починить шкаф, убрать ванную",
                user_id=42,
                now=datetime(2026, 10, 9, 9, 29, tzinfo=timezone.utc),
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["day"], "")

    def test_execute_plan_uses_requested_day_window(self):
        items = [
            {
                "title": "Отправить паспорт Артему",
                "estimate_minutes": 5,
                "category": "personal",
                "priority": "normal",
            },
            {
                "title": "Позвонить Ольхову",
                "estimate_minutes": 15,
                "category": "work",
                "priority": "normal",
            },
        ]
        created = [
            {"task_id": 11, **items[0]},
            {"task_id": 12, **items[1]},
        ]
        proposals = [
            {
                "task_id": 11,
                "title": items[0]["title"],
                "priority": "normal",
                "category": "personal",
                "estimate_minutes": 5,
                "due_at": "2026-10-07T23:59:00+03:00",
                "start": "2026-10-07T09:00:00+03:00",
                "end": "2026-10-07T09:05:00+03:00",
            },
            {
                "task_id": 12,
                "title": items[1]["title"],
                "priority": "normal",
                "category": "work",
                "estimate_minutes": 15,
                "due_at": "2026-10-07T23:59:00+03:00",
                "start": "2026-10-07T09:20:00+03:00",
                "end": "2026-10-07T09:35:00+03:00",
            },
        ]

        with patch("modules.ai_task_planner.get_user_timezone", return_value="Europe/Moscow"), \
             patch("modules.ai_task_planner._list_events", return_value=[]), \
             patch("modules.ai_task_planner.create_planner_task", side_effect=created) as create, \
             patch(
                 "modules.ai_task_planner.preview_flexible_schedule",
                 return_value={"proposals": proposals, "skipped": []},
             ) as preview, \
             patch(
                 "modules.ai_task_planner.apply_task_slot",
                 side_effect=[{"task_id": 11}, {"task_id": 12}],
             ):
            result = execute_task_plan(
                42,
                items,
                date(2026, 10, 7),
                now=datetime(2026, 10, 7, 5, 48, tzinfo=timezone.utc),
            )

        self.assertEqual(create.call_args_list[0].kwargs["estimate_minutes"], 5)
        kwargs = preview.call_args.kwargs
        self.assertEqual(kwargs["window_start"].date(), date(2026, 10, 7))
        self.assertEqual(kwargs["window_end"].date(), date(2026, 10, 8))
        self.assertEqual(len(result["applied"]), 2)
        self.assertEqual(result["unscheduled"], [])


class AITaskPlannerConversationTests(unittest.IsolatedAsyncioTestCase):
    def _update(self):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )

    async def test_bare_list_is_kept_in_shared_pending_context_until_day_is_known(self):
        update = self._update()
        context = SimpleNamespace(user_data={})
        plan = {
            "schedule": False,
            "day": "",
            "items": [
                {"title": "Отправить паспорт", "estimate_minutes": 5, "category": "personal", "priority": "normal"},
                {"title": "Позвонить Ольхову", "estimate_minutes": 15, "category": "work", "priority": "normal"},
            ],
        }
        with patch("modules.ai_task_planner.interpret_task_plan", return_value=plan):
            handled = await handle_unhandled_task_plan(
                update,
                context,
                "Отправить паспорт, позвонить Ольхову",
            )

        self.assertTrue(handled)
        pending = get_pending(context)
        self.assertEqual(pending["type"], "task_plan_date")
        self.assertEqual(len(pending["items"]), 2)
        update.message.reply_text.assert_awaited_once()
        self.assertIn("На какой день", update.message.reply_text.await_args.args[0])

    async def test_pending_list_is_scheduled_when_user_says_today(self):
        update = self._update()
        context = SimpleNamespace(user_data={})
        pending = {
            "type": "task_plan_date",
            "day": None,
            "items": [
                {"title": "Отправить паспорт", "estimate_minutes": 5, "category": "personal", "priority": "normal"},
                {"title": "Позвонить Ольхову", "estimate_minutes": 15, "category": "work", "priority": "normal"},
            ],
        }
        result = {
            "target_day": "2026-10-07",
            "applied": [
                {
                    "task_id": 1,
                    "title": "Отправить паспорт",
                    "estimate_minutes": 5,
                    "start": "2026-10-07T09:00:00+03:00",
                    "end": "2026-10-07T09:05:00+03:00",
                }
            ],
            "unscheduled": [],
        }
        set_pending(context, pending)
        with patch("modules.ai_task_planner._date_from_user_text", return_value=date(2026, 10, 7)), \
             patch("modules.ai_task_planner.execute_task_plan", return_value=result), \
             patch("modules.ai_task_planner.get_user_timezone", return_value="Europe/Moscow"):
            handled = await resume_pending_task_plan(update, context, "сегодня, распланируй", pending)

        self.assertTrue(handled)
        self.assertIsNone(get_pending(context))
        reply = update.message.reply_text.await_args.args[0]
        self.assertIn("09:00–09:05", reply)
        self.assertIn("5 мин", reply)


if __name__ == "__main__":
    unittest.main()
