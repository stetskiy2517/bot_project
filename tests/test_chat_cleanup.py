import unittest
from unittest.mock import patch

import web_app
from tests.web_test_support import web_test_app
from core.db import get_or_create_google_user


class ChatCleanupTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        web_app._user_state.clear()

    def _login(self):
        user_id = get_or_create_google_user(
            "chat-cleanup-user",
            "chat-cleanup@example.test",
            "Chat Cleanup",
        )
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
        return user_id

    def test_collapsed_chat_history_is_removed_from_dom(self):
        response = self.client.get("/reminders.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()

        self.assertIn("new MutationObserver", script)
        self.assertIn('attributeFilter: ["class"]', script)
        self.assertIn("chatNode.replaceChildren()", script)
        self.assertIn("const CHAT_CLEAR_DELAY_MS = 400", script)

    def test_reminder_delivery_stays_independent_from_chat_cleanup(self):
        user_id = self._login()
        reminder = {
            "id": 17,
            "text": "Позвонить клиенту",
            "remind_at": "2026-09-13T09:00:00+03:00",
            "message": "Напоминание · Позвонить клиенту",
        }
        with patch("web_app.claim_due_for_user", return_value=[reminder]) as claim:
            response = self.client.get("/api/reminders/due")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reminders"], [reminder])
        claim.assert_called_once_with(user_id)

    def test_reminder_script_keeps_polling_and_persistent_notifications(self):
        response = self.client.get("/reminders.js")
        script = response.get_data(as_text=True)
        response.close()

        self.assertIn('api("/api/reminders/due", { signal: controller.signal })', script)
        self.assertIn('registration.showNotification("Напоминание"', script)
        self.assertIn("window.setInterval(pollDueReminders, REMINDER_POLL_MS)", script)
        self.assertIn('window.addEventListener("pagehide"', script)
        self.assertIn("reminderPollController?.abort()", script)


if __name__ == "__main__":
    unittest.main()
