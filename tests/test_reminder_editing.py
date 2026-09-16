from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

import web_app
from core.db import get_or_create_google_user
from core.library_store import get_saved_reminder
from core.reminder_store import create_reminder
from modules.reminder_categories import reminder_category
from tests.web_test_support import web_test_app


class ReminderEditingTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        web_app._user_state.clear()
        stamp = uuid4().hex
        self.user_id = get_or_create_google_user(
            f"reminder-edit-{stamp}",
            f"reminder-edit-{stamp}@example.test",
            "Reminder Editor",
        )
        self.other_user_id = get_or_create_google_user(
            f"reminder-edit-other-{stamp}",
            f"reminder-edit-other-{stamp}@example.test",
            "Other Reminder Editor",
        )
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id
            stored.permanent = True

    def _create(self, text: str = "Позвонить клиенту") -> dict:
        return create_reminder(
            self.user_id,
            text,
            datetime.now(timezone.utc) + timedelta(hours=2),
        )

    def test_details_expose_automatic_category(self):
        reminder = self._create()
        response = self.client.get(
            f"/api/mobile/reminders/{reminder['reminder_id']}/details"
        )
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["reminder"]
        self.assertEqual(item["category"], "work")
        self.assertEqual(item["category_source"], "auto")
        self.assertEqual(item["category_label"], "Работа")

    def test_manual_category_has_priority_and_can_return_to_auto(self):
        reminder = self._create()
        reminder_id = reminder["reminder_id"]

        response = self.client.patch(
            f"/api/mobile/reminders/{reminder_id}/details",
            json={"category": "personal"},
        )
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["reminder"]
        self.assertEqual(item["category"], "personal")
        self.assertEqual(item["category_source"], "manual")
        self.assertEqual(reminder_category(get_saved_reminder(self.user_id, reminder_id)), "personal")

        response = self.client.patch(
            f"/api/mobile/reminders/{reminder_id}/details",
            json={"category": "auto"},
        )
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["reminder"]
        self.assertEqual(item["category"], "work")
        self.assertEqual(item["category_source"], "auto")

    def test_text_and_repeat_can_be_edited(self):
        reminder = self._create()
        reminder_id = reminder["reminder_id"]
        response = self.client.patch(
            f"/api/mobile/reminders/{reminder_id}/details",
            json={"text": "Принять лекарство", "repeat_rule": "daily"},
        )
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["reminder"]
        self.assertEqual(item["text"], "Принять лекарство")
        self.assertEqual(item["repeat_rule"], "daily")
        self.assertEqual(item["category"], "health")
        stored = get_saved_reminder(self.user_id, reminder_id)
        self.assertEqual(stored["text"], "Принять лекарство")
        self.assertEqual(stored["repeat_rule"], "daily")
        self.assertIsNotNone(stored["next_remind_at"])

        response = self.client.patch(
            f"/api/mobile/reminders/{reminder_id}/details",
            json={"repeat_rule": "none"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["reminder"]["repeat_rule"])
        self.assertIsNone(get_saved_reminder(self.user_id, reminder_id)["next_remind_at"])

    def test_detail_edit_rejects_other_users_reminder(self):
        other = create_reminder(
            self.other_user_id,
            "Не менять",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        response = self.client.patch(
            f"/api/mobile/reminders/{other['reminder_id']}/details",
            json={"category": "work"},
        )
        self.assertEqual(response.status_code, 404)

    def test_invalid_category_and_empty_text_are_rejected(self):
        reminder = self._create()
        path = f"/api/mobile/reminders/{reminder['reminder_id']}/details"
        self.assertEqual(self.client.patch(path, json={"category": "secret"}).status_code, 400)
        self.assertEqual(self.client.patch(path, json={"text": "   "}).status_code, 400)

    def test_mobile_shell_loads_reminder_editor(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<script src="/reminder-editor.js"></script>', response.get_data(as_text=True))
        asset = self.client.get("/reminder-editor.js")
        self.assertEqual(asset.status_code, 200)
        script = asset.get_data(as_text=True)
        for marker in (
            "data-reminder-edit",
            "reminder-category-chip",
            "/api/mobile/reminders/details",
            'method: "PATCH"',
            "Авто ·",
            "Повтор",
        ):
            self.assertIn(marker, script)


if __name__ == "__main__":
    unittest.main()
