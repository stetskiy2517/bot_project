from __future__ import annotations

import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import web_app
from core.db import conn, db_lock, get_or_create_google_user
from core.user_activity_store import list_user_activity
from tests.web_test_support import web_test_app


class UserActivityDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        token = uuid.uuid4().hex
        self.app = web_test_app()
        self.client = self.app.test_client()
        self.user_id = get_or_create_google_user(
            f"diag-{token}", f"diag-{token}@example.test", "Diagnostics"
        )
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id
            stored["auth_time"] = time.time()

    def tearDown(self):
        with db_lock:
            conn.execute("DELETE FROM user_activity_log WHERE user_id=?", (self.user_id,))
            conn.execute("DELETE FROM google_accounts WHERE user_id=?", (self.user_id,))
            conn.execute("DELETE FROM users WHERE user_id=?", (self.user_id,))
            conn.commit()

    def test_chat_request_records_command_and_result_without_secrets(self):
        result = web_app.WebPlannerResult(
            handled=True,
            replies=["Готово"],
            choices=[{"label": "Вариант"}],
        )
        with patch.object(
            web_app,
            "process_web_message",
            new=AsyncMock(return_value=result),
        ), patch.object(
            web_app,
            "_with_due_reminders",
            side_effect=lambda _user_id, replies: replies,
        ):
            response = self.client.post(
                "/api/chat",
                json={"message": "встреча сегодня в 18:30"},
            )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        events = list_user_activity(self.user_id)
        chat = next(item for item in events if item["path"] == "/api/chat")
        self.assertEqual(chat["status"], 200)
        self.assertEqual(chat["phase"], "done")
        self.assertEqual(chat["details"]["message"], "встреча сегодня в 18:30")
        self.assertTrue(chat["details"]["handled"])
        self.assertEqual(chat["details"]["choices_count"], 1)
        self.assertEqual(chat["details"]["replies_count"], 1)
        self.assertNotIn("csrf", str(chat).lower())

    def test_client_event_is_recorded_as_structured_activity(self):
        response = self.client.post(
            "/api/client/activity",
            json={
                "event": "view_change",
                "details": {
                    "from": "home",
                    "view": "chat",
                    "source": "navigation",
                    "secret": "must-not-be-kept",
                },
            },
        )
        self.assertEqual(response.status_code, 204)

        event = list_user_activity(self.user_id)[0]
        self.assertEqual(event["phase"], "client")
        self.assertEqual(event["details"]["event"], "view_change")
        self.assertEqual(event["details"]["from"], "home")
        self.assertEqual(event["details"]["view"], "chat")
        self.assertNotIn("secret", event["details"])


if __name__ == "__main__":
    unittest.main()
