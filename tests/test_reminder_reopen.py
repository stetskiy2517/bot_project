from tests.web_client import create_test_app, set_test_session
from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

import web_app
from core.ai_memory_store import list_ai_memory_events
from core.db import get_or_create_google_user
from core.reminder_store import claim_due_reminders, create_reminder


class ReminderReopenTests(unittest.TestCase):
    def setUp(self):
        self.app = create_test_app()
        self.client = self.app.test_client()
        web_app._user_state.clear()
        stamp = uuid4().hex
        self.user_id = get_or_create_google_user(
            f"reminder-reopen-{stamp}",
            f"reminder-reopen-{stamp}@example.test",
            "Reminder Reopen User",
        )
        self.other_user_id = get_or_create_google_user(
            f"reminder-reopen-other-{stamp}",
            f"reminder-reopen-other-{stamp}@example.test",
            "Reminder Reopen Other",
        )
        with self.client.session_transaction() as session:
            set_test_session(session, self.user_id)
            session.permanent = True

    def test_future_completed_reminder_can_be_returned_to_pending(self):
        reminder = create_reminder(
            self.user_id,
            "Выпить таблетку",
            datetime.now(timezone.utc) + timedelta(hours=2),
        )
        completed = self.client.post(
            f"/api/library/reminders/{reminder['reminder_id']}/complete"
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.get_json()["reminder"]["status"], "completed")
        completed.close()

        reopened = self.client.post(
            f"/api/library/reminders/{reminder['reminder_id']}/complete",
            json={"completed": False},
        )
        self.assertEqual(reopened.status_code, 200)
        payload = reopened.get_json()["reminder"]
        reopened.close()
        self.assertEqual(payload["status"], "pending")
        self.assertIsNone(payload["completed_at"])
        self.assertIsNone(payload["delivered_at"])

        events = [
            item
            for item in list_ai_memory_events(self.user_id, limit=50)
            if item["entity_type"] == "reminder"
            and item["entity_id"] == reminder["reminder_id"]
        ]
        self.assertEqual(events[0]["event_type"], "reopened")
        self.assertEqual(events[0]["snapshot"]["status"], "pending")
        self.assertTrue(any(item["event_type"] == "completed" for item in events))

    def test_past_completed_reminder_reopens_without_second_notification(self):
        now = datetime.now(timezone.utc)
        reminder = create_reminder(
            self.user_id,
            "Проверить лекарство",
            now - timedelta(minutes=5),
        )
        due = claim_due_reminders(self.user_id, now=now)
        self.assertTrue(any(item["reminder_id"] == reminder["reminder_id"] for item in due))

        self.client.post(f"/api/library/reminders/{reminder['reminder_id']}/complete").close()
        reopened = self.client.post(
            f"/api/library/reminders/{reminder['reminder_id']}/complete",
            json={"completed": False},
        )
        self.assertEqual(reopened.status_code, 200)
        payload = reopened.get_json()["reminder"]
        reopened.close()
        self.assertEqual(payload["status"], "delivered")
        self.assertIsNone(payload["completed_at"])

        due_again = claim_due_reminders(self.user_id, now=now + timedelta(minutes=1))
        self.assertFalse(
            any(item["reminder_id"] == reminder["reminder_id"] for item in due_again)
        )

    def test_reopen_is_scoped_to_current_user(self):
        reminder = create_reminder(
            self.other_user_id,
            "Чужое выполненное напоминание",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        response = self.client.post(
            f"/api/library/reminders/{reminder['reminder_id']}/complete",
            json={"completed": False},
        )
        self.assertEqual(response.status_code, 404)
        response.close()

    def test_completion_state_requires_boolean(self):
        reminder = create_reminder(
            self.user_id,
            "Проверить тип",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        response = self.client.post(
            f"/api/library/reminders/{reminder['reminder_id']}/complete",
            json={"completed": "false"},
        )
        self.assertEqual(response.status_code, 400)
        response.close()

    def test_library_exposes_return_action_for_completed_reminders(self):
        response = self.client.get("/library.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()
        self.assertIn('reopenButton.dataset.action = "reopen"', script)
        self.assertIn('reopenButton.textContent = "Вернуть"', script)
        self.assertIn("async function reopenReminder(reminderId)", script)
        self.assertIn('JSON.stringify({ completed: false })', script)
        self.assertIn('else if (action === "reopen") reopenReminder(itemId)', script)
        self.assertIn('row.dataset.leftWidth = "176"', script)


if __name__ == "__main__":
    unittest.main()
