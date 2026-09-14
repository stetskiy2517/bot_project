from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from core.db import get_or_create_google_user
from core.reminder_store import create_reminder
from tests.web_test_support import web_test_app


class ReminderRepeatIconTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        stamp = uuid4().hex
        self.user_id = get_or_create_google_user(
            f"repeat-icon-{stamp}",
            f"repeat-icon-{stamp}@example.test",
            "Repeat Icon User",
        )
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id
            session.permanent = True

    def test_library_api_exposes_repeat_rule(self):
        recurring = create_reminder(
            self.user_id,
            "Выпить таблетку",
            datetime.now(timezone.utc) + timedelta(hours=1),
            repeat_rule="daily",
            repeat_timezone="Europe/Moscow",
        )
        one_off = create_reminder(
            self.user_id,
            "Позвонить врачу",
            datetime.now(timezone.utc) + timedelta(hours=2),
        )

        response = self.client.get("/api/library")
        self.assertEqual(response.status_code, 200)
        reminders = {item["id"]: item for item in response.get_json()["reminders"]}
        response.close()

        self.assertEqual(reminders[recurring["reminder_id"]]["repeat_rule"], "daily")
        self.assertIsNone(reminders[one_off["reminder_id"]]["repeat_rule"])

    def test_library_script_uses_icon_without_repeat_label(self):
        response = self.client.get("/library.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()

        for marker in [
            'if (reminder.repeat_rule)',
            'repeatIcon.className = "reminder-repeat-icon"',
            'repeatIcon.textContent = "↻"',
            'meta.className = "library-card-meta reminder-card-meta"',
        ]:
            self.assertIn(marker, script)
        self.assertNotIn("Ежедневно", script)
        self.assertNotIn("Каждый день", script)


if __name__ == "__main__":
    unittest.main()
