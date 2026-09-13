import unittest
from unittest.mock import patch

from modules.reminders import delete_reminder_from_text, resume_pending_reminder


class ReminderDeleteContextTests(unittest.IsolatedAsyncioTestCase):
    class Context:
        def __init__(self):
            self.user_data = {}

    class Message:
        def __init__(self):
            self.replies = []

        async def reply_text(self, text):
            self.replies.append(text)

    class User:
        id = 1

    class Update:
        def __init__(self):
            self.message = ReminderDeleteContextTests.Message()
            self.effective_user = ReminderDeleteContextTests.User()

    async def test_delete_without_target_stores_pending_context(self):
        reminder = {
            "reminder_id": 7,
            "text": "Выпить таблетку",
            "remind_at": "2026-09-13T22:34:00+03:00",
        }
        context = self.Context()
        context.user_data["smart_planner_last_reminder"] = {
            "reminder_id": 7,
            "text": "Выпить таблетку",
        }
        update = self.Update()

        with patch("modules.reminders.get_user_timezone", return_value="Europe/Moscow"), patch(
            "modules.reminders.list_active_reminders", return_value=[reminder]
        ):
            handled = await delete_reminder_from_text(update, context, "удали напоминание")

        self.assertTrue(handled)
        self.assertEqual(update.message.replies[-1], "Какое напоминание удалить?")
        pending = context.user_data.get("smart_planner_pending")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["type"], "reminder_delete_query")
        self.assertEqual(pending["reference"]["reminder_id"], 7)

    async def test_this_deletes_referenced_reminder(self):
        reminder = {
            "reminder_id": 7,
            "text": "Выпить таблетку",
            "remind_at": "2026-09-13T22:34:00+03:00",
        }
        pending = {
            "type": "reminder_delete_query",
            "timezone": "Europe/Moscow",
            "reference": reminder,
        }
        context = self.Context()
        context.user_data["smart_planner_pending"] = pending
        update = self.Update()

        with patch("modules.reminders.list_active_reminders", return_value=[reminder]), patch(
            "modules.reminders.delete_reminder", return_value=True
        ) as delete:
            handled = await resume_pending_reminder(update, context, "это", pending)

        self.assertTrue(handled)
        delete.assert_called_once_with(1, 7)
        self.assertNotIn("smart_planner_pending", context.user_data)
        self.assertEqual(update.message.replies[-1], "Напоминание «Выпить таблетку» удалено.")

    async def test_this_with_ambiguous_context_asks_for_number(self):
        reminders = [
            {"reminder_id": 1, "text": "Первое", "remind_at": "2026-09-14T09:00:00+03:00"},
            {"reminder_id": 2, "text": "Второе", "remind_at": "2026-09-14T10:00:00+03:00"},
        ]
        pending = {
            "type": "reminder_delete_query",
            "timezone": "Europe/Moscow",
            "reference": None,
        }
        context = self.Context()
        context.user_data["smart_planner_pending"] = pending
        update = self.Update()

        with patch("modules.reminders.list_active_reminders", return_value=reminders):
            handled = await resume_pending_reminder(update, context, "это", pending)

        self.assertTrue(handled)
        self.assertIn("Не понял, какое именно. Напиши номер:", update.message.replies[-1])
        self.assertEqual(context.user_data["smart_planner_pending"]["type"], "reminder_select_delete")


if __name__ == "__main__":
    unittest.main()
