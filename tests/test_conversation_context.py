from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.conversation_context import (
    clear_current_entity,
    clear_pending,
    context_snapshot,
    current_entity,
    current_entity_for_user,
    clear_user_state,
    get_pending,
    recent_entities,
    remember_entity,
    set_pending,
)
from modules import router_core
from modules.reminders import REMINDER_UPDATE
from modules.tasks import TASK_DELETE


class UnifiedConversationContextTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace(user_data={})

    def test_one_current_entity_replaces_module_specific_focus(self):
        remember_entity(self.context, "note", 10, "Покупки")
        self.assertEqual(current_entity(self.context)["type"], "note")
        remember_entity(self.context, "calendar_event", "evt-1", "Маникюр")
        current = current_entity(self.context)
        self.assertEqual(current["type"], "calendar_event")
        self.assertEqual(current["id"], "evt-1")
        self.assertEqual([item["type"] for item in recent_entities(self.context)[:2]], ["calendar_event", "note"])

    def test_recent_entities_deduplicate_same_object(self):
        remember_entity(self.context, "task", 7, "Отчёт")
        remember_entity(self.context, "task", 7, "Отчёт обновлён")
        items = recent_entities(self.context, "task")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Отчёт обновлён")

    def test_entity_can_be_cleared_without_touching_pending(self):
        remember_entity(self.context, "reminder", 4, "Таблетки")
        set_pending(self.context, {"type": "reminder_edit", "reminder_id": 4})
        clear_current_entity(self.context, "reminder", 4)
        self.assertIsNone(current_entity(self.context))
        self.assertEqual(get_pending(self.context)["type"], "reminder_edit")
        clear_pending(self.context)
        self.assertIsNone(get_pending(self.context))

    def test_snapshot_uses_single_schema(self):
        remember_entity(self.context, "task", 1, "Планёрка")
        set_pending(self.context, {"type": "task_confirm_delete"})
        snapshot = context_snapshot(self.context)
        self.assertEqual(snapshot["version"], 1)
        self.assertEqual(snapshot["current_entity"]["type"], "task")
        self.assertEqual(snapshot["pending"]["type"], "task_confirm_delete")


class UnifiedWebContextPersistenceTests(unittest.TestCase):
    def tearDown(self):
        clear_user_state()

    def test_first_api_access_restores_durable_context_before_reading_focus(self):
        stored = {
            "smart_planner_context": {
                "version": 1,
                "current_entity": {
                    "type": "task",
                    "id": "77",
                    "title": "Отчёт",
                    "touched_at": 9999999999.0,
                },
                "recent_entities": [],
                "pending": {"type": "task_confirm_delete"},
            }
        }
        with patch("core.command_store.load_conversation", return_value=stored) as load:
            reference = current_entity_for_user(424242)
        self.assertEqual(reference["id"], "77")
        load.assert_called_once_with(424242)


class UnifiedContextRoutingTests(unittest.IsolatedAsyncioTestCase):
    def _update(self, text: str):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
        )

    async def test_generic_reminder_followup_uses_current_reminder(self):
        context = SimpleNamespace(user_data={})
        remember_entity(context, "reminder", 8, "Купить лекарства")
        update = self._update("перенеси на завтра в 9")
        with patch("modules.router_core.handle_reminder_text", new=AsyncMock(return_value=True)) as handler:
            handled = await router_core._route_current_entity_action(update, context, update.message.text)
        self.assertTrue(handled)
        args = handler.await_args.args
        self.assertEqual(args[3], REMINDER_UPDATE)
        self.assertIn("Купить лекарства", args[2])
        self.assertIn("завтра в 9", args[2])

    async def test_generic_task_delete_uses_current_task_not_calendar(self):
        context = SimpleNamespace(user_data={})
        remember_entity(context, "task", 3, "Планерка + статистика для отчета")
        update = self._update("удали её")
        with patch("modules.router_core.handle_task_text", new=AsyncMock(return_value=True)) as handler:
            handled = await router_core._route_current_entity_action(update, context, update.message.text)
        self.assertTrue(handled)
        args = handler.await_args.args
        self.assertEqual(args[3], TASK_DELETE)
        self.assertIn("Планерка + статистика для отчета", args[2])

    async def test_generic_task_update_uses_focused_task_id(self):
        context = SimpleNamespace(user_data={})
        remember_entity(context, "task", 12, "Отчёт")
        update = self._update("перенеси её на завтра в 10")
        with patch("modules.router_core.update_task_from_text", new=AsyncMock(return_value=True)) as handler:
            handled = await router_core._route_current_entity_action(update, context, update.message.text)
        self.assertTrue(handled)
        handler.assert_awaited_once_with(update, context, update.message.text, task_id=12)

    async def test_event_property_followup_beats_note_append_heuristics(self):
        context = SimpleNamespace(user_data={})
        remember_entity(context, "calendar_event", "evt-8", "Встреча")
        update = self._update("и добавь адрес Ленина 5")
        with patch("modules.router_core.update_from_text", new=AsyncMock(return_value=True)) as handler:
            handled = await router_core._route_current_entity_action(update, context, update.message.text)
        self.assertTrue(handled)
        self.assertEqual(handler.await_args.kwargs["event_id"], "evt-8")

    async def test_relative_reminder_attaches_to_focused_event(self):
        context = SimpleNamespace(user_data={})
        remember_entity(context, "calendar_event", "evt-7", "Маникюр")
        update = self._update("напомни за час")
        with patch("modules.router_core.update_from_text", new=AsyncMock(return_value=True)) as handler:
            handled = await router_core._route_current_entity_action(update, context, update.message.text)
        self.assertTrue(handled)
        self.assertEqual(handler.await_args.kwargs["event_id"], "evt-7")

    async def test_absolute_reminder_is_not_claimed_by_focused_event(self):
        context = SimpleNamespace(user_data={})
        remember_entity(context, "calendar_event", "evt-7", "Маникюр")
        update = self._update("напомни завтра в 9 купить молоко")
        with patch("modules.router_core.update_from_text", new=AsyncMock(return_value=True)) as handler:
            handled = await router_core._route_current_entity_action(update, context, update.message.text)
        self.assertFalse(handled)
        handler.assert_not_awaited()

    async def test_explicit_other_entity_does_not_use_stale_focus(self):
        context = SimpleNamespace(user_data={})
        remember_entity(context, "reminder", 8, "Купить лекарства")
        update = self._update("удали задачу отчет")
        handled = await router_core._route_current_entity_action(update, context, update.message.text)
        self.assertFalse(handled)


if __name__ == "__main__":
    unittest.main()
