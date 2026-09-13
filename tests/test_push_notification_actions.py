import unittest
from pathlib import Path
from unittest.mock import patch

from modules import reminder_dispatcher


ROOT = Path(__file__).resolve().parents[1]


class PushReminderActionTests(unittest.TestCase):
    def test_reminder_payload_has_complete_and_reschedule_actions(self):
        with patch.object(reminder_dispatcher, "BASE_URL", "https://assistant.example"):
            payload = reminder_dispatcher._notification_payload(
                title="Напоминание",
                body="Выпить таблетку",
                tag="reminder-42",
                reminder_id=42,
            )

        notification = payload["notification"]
        self.assertEqual(
            [(item["action"], item["title"]) for item in notification["actions"]],
            [("complete", "Выполнено"), ("reschedule", "Отложить")],
        )
        self.assertEqual(
            notification["actions"][0]["navigate"],
            "https://assistant.example/?push_action=complete&reminder_id=42",
        )
        self.assertEqual(
            notification["actions"][1]["navigate"],
            "https://assistant.example/?push_action=reschedule&reminder_id=42",
        )
        self.assertEqual(
            notification["data"]["action_urls"],
            {
                "complete": "/?push_action=complete&reminder_id=42",
                "reschedule": "/?push_action=reschedule&reminder_id=42",
            },
        )

    def test_test_push_stays_without_reminder_actions(self):
        payload = reminder_dispatcher._notification_payload(
            title="Уведомления работают",
            body="Тест",
            tag="push-test",
        )
        self.assertNotIn("actions", payload["notification"])
        self.assertNotIn("action_urls", payload["notification"]["data"])

    def test_service_worker_preserves_actions_and_has_click_fallback(self):
        worker = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("notification.actions", worker)
        self.assertIn("event.action", worker)
        self.assertIn("actionUrls", worker)
        self.assertIn("notificationTargetUrl", worker)

    def test_web_client_connects_push_actions_to_existing_reminder_ui(self):
        script = (ROOT / "web" / "reminders.js").read_text(encoding="utf-8")
        self.assertIn("push_action=complete", script)
        self.assertIn("push_action=reschedule", script)
        self.assertIn("handlePushActionFromUrl", script)
        self.assertIn("/complete`, { method: \"POST\" }", script)
        self.assertIn("[data-action=\"reschedule\"]", script)
        self.assertIn("Выполнено", script)
        self.assertIn("Отложить", script)


if __name__ == "__main__":
    unittest.main()
