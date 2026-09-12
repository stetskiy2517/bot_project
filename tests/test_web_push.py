import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import web_app
from core.db import get_or_create_google_user
from core.push_store import delete_push_subscription, list_push_subscriptions
from core.reminder_store import (
    REMINDER_DELIVERED,
    claim_due_for_push,
    complete_push_delivery,
    create_reminder,
    list_reminders,
    release_push_delivery,
)
from integrations import web_push
from modules import reminder_dispatcher


class WebPushApiTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()
        suffix = str(time.time_ns())
        self.user_id = get_or_create_google_user(
            f"push-{suffix}", f"push-{suffix}@example.test", "Push Test"
        )
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id
        self.endpoint = f"https://push.example.test/{suffix}"

    def tearDown(self):
        delete_push_subscription(self.user_id, self.endpoint)

    def test_push_config_requires_session_and_returns_public_key(self):
        other = self.app.test_client()
        self.assertEqual(other.get("/api/push/config").status_code, 401)
        with patch("web_app.get_vapid_public_key", return_value="public-key"):
            response = self.client.get("/api/push/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["public_key"], "public-key")

    def test_subscription_can_be_saved_and_removed_for_current_user(self):
        payload = {
            "endpoint": self.endpoint,
            "keys": {"p256dh": "p256dh-test", "auth": "auth-test"},
        }
        response = self.client.post("/api/push/subscriptions", json=payload)
        self.assertEqual(response.status_code, 200)
        subscriptions = list_push_subscriptions(self.user_id)
        self.assertTrue(any(item["endpoint"] == self.endpoint for item in subscriptions))

        response = self.client.delete(
            "/api/push/subscriptions", json={"endpoint": self.endpoint}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertFalse(any(item["endpoint"] == self.endpoint for item in list_push_subscriptions(self.user_id)))

    def test_pwa_shell_loads_push_client(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('<script src="/reminders.js"></script>', html)
        script = self.client.get("/reminders.js").get_data(as_text=True)
        self.assertIn("pushManager.subscribe", script)
        self.assertIn("Notification.requestPermission", script)
        self.assertIn("Включить уведомления", script)
        worker = self.client.get("/sw.js").get_data(as_text=True)
        self.assertIn('addEventListener("push"', worker)
        self.assertIn("showNotification", worker)
        self.assertIn('addEventListener("notificationclick"', worker)


class VapidKeyTests(unittest.TestCase):
    def test_vapid_key_is_generated_once_and_public_key_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            private_path = Path(tmp) / "vapid.pem"
            with patch.object(web_push, "WEB_PUSH_VAPID_PRIVATE_KEY", str(private_path)):
                first = web_push.get_vapid_public_key()
                second = web_push.get_vapid_public_key()
            self.assertTrue(private_path.exists())
            self.assertEqual(first, second)
            self.assertGreater(len(first), 80)


class ReminderPushLeaseTests(unittest.TestCase):
    def test_failed_push_can_be_released_and_retried(self):
        user_id = 900_000_000 + (time.time_ns() % 10_000_000)
        reminder = create_reminder(
            user_id,
            "Проверить напоминание",
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        claimed = claim_due_for_push([user_id], limit=10)
        target = next(item for item in claimed if item["reminder_id"] == reminder["reminder_id"])
        self.assertEqual(target["status"], "delivering")

        self.assertTrue(release_push_delivery(reminder["reminder_id"], "temporary"))
        claimed_again = claim_due_for_push([user_id], limit=10)
        self.assertTrue(any(item["reminder_id"] == reminder["reminder_id"] for item in claimed_again))
        self.assertTrue(complete_push_delivery(reminder["reminder_id"]))
        delivered = list_reminders(user_id, status=REMINDER_DELIVERED, limit=20)
        self.assertTrue(any(item["reminder_id"] == reminder["reminder_id"] for item in delivered))


class ReminderDispatcherTests(unittest.TestCase):
    def test_expired_subscription_is_removed_and_reminder_released(self):
        reminder = {"reminder_id": 44, "user_id": 7, "text": "Тест"}
        subscription = {
            "subscription_id": 9,
            "user_id": 7,
            "endpoint": "https://push.example.test/expired",
            "p256dh": "key",
            "auth": "auth",
        }

        class Response:
            status_code = 410

        from pywebpush import WebPushException

        error = WebPushException("expired", response=Response())
        with patch.object(reminder_dispatcher, "list_push_user_ids", return_value=[7]), \
             patch.object(reminder_dispatcher, "claim_due_for_push", return_value=[reminder]), \
             patch.object(reminder_dispatcher, "list_push_subscriptions", return_value=[subscription]), \
             patch.object(reminder_dispatcher, "send_web_push", side_effect=error), \
             patch.object(reminder_dispatcher, "delete_push_subscription_by_id") as remove, \
             patch.object(reminder_dispatcher, "release_push_delivery") as release:
            stats = reminder_dispatcher.dispatch_due_reminders_once()

        remove.assert_called_once_with(9)
        release.assert_called_once()
        self.assertEqual(stats["subscriptions_removed"], 1)
        self.assertEqual(stats["released"], 1)


if __name__ == "__main__":
    unittest.main()
