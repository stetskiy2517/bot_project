import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import web_app
from tests.web_test_support import web_test_app, push_keys
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
        self.app = web_test_app()
        self.client = self.app.test_client()
        suffix = str(time.time_ns())
        self.user_id = get_or_create_google_user(
            f"push-{suffix}", f"push-{suffix}@example.test", "Push Test"
        )
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id
        self.endpoint = f"https://fcm.googleapis.com/fcm/send/{suffix}"

    def tearDown(self):
        delete_push_subscription(self.user_id, self.endpoint)

    def _subscribe(self):
        payload = {
            "endpoint": self.endpoint,
            "keys": push_keys(),
        }
        response = self.client.post("/api/push/subscriptions", json=payload)
        self.assertEqual(response.status_code, 200)
        return response

    def test_push_config_requires_session_and_returns_public_key(self):
        other = self.app.test_client()
        self.assertEqual(other.get("/api/push/config").status_code, 401)
        with patch("web_app.get_vapid_public_key", return_value="public-key"):
            response = self.client.get("/api/push/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["public_key"], "public-key")

    def test_subscription_can_be_saved_and_removed_for_current_user(self):
        self._subscribe()
        subscriptions = list_push_subscriptions(self.user_id)
        self.assertTrue(any(item["endpoint"] == self.endpoint for item in subscriptions))

        response = self.client.delete(
            "/api/push/subscriptions", json={"endpoint": self.endpoint}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertFalse(any(item["endpoint"] == self.endpoint for item in list_push_subscriptions(self.user_id)))

    def test_push_status_is_scoped_to_current_user(self):
        self._subscribe()
        response = self.client.get("/api/push/status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["subscribed"])
        self.assertEqual(payload["subscriptions"], 1)
        self.assertEqual(len(payload["devices"]), 1)
        self.assertNotIn("endpoint", payload["devices"][0])

    def test_push_test_returns_accepted_result(self):
        result = {
            "ok": True,
            "subscriptions": 1,
            "accepted": 1,
            "failed": 0,
            "removed": 0,
            "errors": [],
        }
        with patch("web_app.send_test_push_for_user", return_value=result) as send:
            response = self.client.post("/api/push/test")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        send.assert_called_once_with(self.user_id)

    def test_push_test_surfaces_apple_bad_jwt(self):
        result = {
            "ok": False,
            "subscriptions": 1,
            "accepted": 0,
            "failed": 1,
            "removed": 0,
            "errors": ['Web Push error 403: {"reason":"BadJwtToken"}'],
        }
        with patch("web_app.send_test_push_for_user", return_value=result):
            response = self.client.post("/api/push/test")
        self.assertEqual(response.status_code, 502)
        self.assertIn("BadJwtToken", response.get_json()["message"])

    def test_foreground_poll_does_not_steal_reminder_from_push_dispatcher(self):
        self._subscribe()
        with patch("web_app.claim_due_for_user") as claim:
            response = self.client.get("/api/reminders/due")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"reminders": []})
        claim.assert_not_called()

    def test_pwa_shell_loads_push_client(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        response.close()
        self.assertIn('<script src="/reminders.js"></script>', html)
        self.assertIn('<script src="/library.js"></script>', html)

        response = self.client.get("/reminders.js")
        script = response.get_data(as_text=True)
        response.close()
        self.assertIn("pushManager.subscribe", script)
        self.assertIn("applicationServerKey", script)
        self.assertIn("directly from the user gesture", script)
        self.assertIn("declarativePushSupported", script)
        self.assertNotIn("const permission = await Notification.requestPermission()", script)
        self.assertIn("Включить уведомления", script)
        self.assertIn("Проверить уведомления", script)
        self.assertIn('api("/api/push/test"', script)

        response = self.client.get("/sw.js")
        worker = response.get_data(as_text=True)
        response.close()
        self.assertIn('addEventListener("push"', worker)
        self.assertIn("showNotification", worker)
        self.assertIn('addEventListener("notificationclick"', worker)
        self.assertIn('personal-secretary-v11-life-wheel-reminders', worker)
        self.assertIn('"/library.js"', worker)
        self.assertIn('"/life-wheel.js"', worker)
        self.assertIn("payload.web_push === 8030", worker)
        self.assertIn("payload.notification", worker)
        self.assertNotIn('icon: "/icon.svg"', worker)


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

    def test_vapid_subject_uses_public_base_url_origin(self):
        with patch.object(web_push, "WEB_PUSH_SUBJECT", None), \
             patch.object(web_push, "BASE_URL", "https://213.171.26.210.sslip.io/path"):
            self.assertEqual(web_push._vapid_subject(), "https://213.171.26.210.sslip.io")

    def test_vapid_subject_rejects_localhost_fallback(self):
        with patch.object(web_push, "WEB_PUSH_SUBJECT", None), \
             patch.object(web_push, "BASE_URL", "http://localhost:8080"):
            with self.assertRaises(RuntimeError):
                web_push._vapid_subject()


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
    def test_declarative_payload_has_browser_fallback_content(self):
        with patch.object(reminder_dispatcher, "BASE_URL", "https://assistant.example"):
            payload = reminder_dispatcher._notification_payload(
                title="Напоминание",
                body="Проверить отчёт",
                tag="reminder-12",
                reminder_id=12,
            )
        self.assertEqual(payload["web_push"], 8030)
        notification = payload["notification"]
        self.assertEqual(notification["title"], "Напоминание")
        self.assertEqual(notification["body"], "Проверить отчёт")
        self.assertEqual(notification["navigate"], "https://assistant.example/")
        self.assertFalse(notification["silent"])
        self.assertEqual(notification["data"]["reminder_id"], 12)

    def test_expired_subscription_is_removed_and_reminder_released(self):
        reminder = {"reminder_id": 44, "user_id": 7, "text": "Тест", "status": "delivering"}
        subscription = {
            "subscription_id": 9,
            "user_id": 7,
            "endpoint": "https://fcm.googleapis.com/fcm/send/expired",
            "p256dh": "key",
            "auth": "auth",
        }

        class Response:
            status_code = 410
            text = "gone"

        from pywebpush import WebPushException

        error = WebPushException("expired", response=Response())
        with patch.object(reminder_dispatcher, "get_google_account", return_value={"user_id": 7}), \
             patch.object(reminder_dispatcher, "get_saved_reminder", return_value=reminder), \
             patch.object(reminder_dispatcher, "claim_repeat_attempts", return_value=[]), \
             patch.object(reminder_dispatcher, "deliver_reviews_for_user", return_value=0), \
             patch.object(reminder_dispatcher, "list_push_user_ids", return_value=[7]), \
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

    def test_push_test_records_success_and_uses_declarative_payload(self):
        subscription = {
            "subscription_id": 3,
            "user_id": 7,
            "endpoint": "https://fcm.googleapis.com/fcm/send/ok",
            "p256dh": "key",
            "auth": "auth",
        }
        with patch.object(reminder_dispatcher, "BASE_URL", "https://assistant.example"), \
             patch.object(reminder_dispatcher, "list_push_subscriptions", return_value=[subscription]), \
             patch.object(reminder_dispatcher, "send_web_push") as send, \
             patch.object(reminder_dispatcher, "mark_push_success") as success:
            result = reminder_dispatcher.send_test_push_for_user(7)
        self.assertTrue(result["ok"])
        self.assertEqual(result["accepted"], 1)
        success.assert_called_once_with(3)
        send.assert_called_once()
        payload = send.call_args.args[1]
        self.assertEqual(payload["web_push"], 8030)
        self.assertEqual(payload["notification"]["title"], "Уведомления работают")
        self.assertEqual(payload["notification"]["navigate"], "https://assistant.example/")


if __name__ == "__main__":
    unittest.main()
