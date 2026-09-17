from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import web_app
from core.db import get_or_create_google_user
from core.reminder_store import create_reminder
from core.web_push_store import list_push_subscriptions
from modules import web_push
from modules.web_push import _dispatch_due_reminders, get_vapid_public_key


class WebPushApiTests(unittest.TestCase):
    def setUp(self):
        self.client = web_app.app.test_client()
        self.user_id = get_or_create_google_user("push-test", "push@example.test", "Push Test")
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id
            session["auth_time"] = 1

    def test_push_config_requires_session_and_returns_public_key(self):
        public = self.client.get("/api/push/config")
        self.assertEqual(public.status_code, 200)
        payload = public.get_json()
        self.assertTrue(payload["public_key"])

        anonymous = web_app.app.test_client().get("/api/push/config")
        self.assertEqual(anonymous.status_code, 401)

    def test_subscription_can_be_saved_and_removed_for_current_user(self):
        payload = {
            "endpoint": "https://web.push.apple.com/Q12345abcdef",
            "keys": {"p256dh": "A" * 43, "auth": "B" * 22},
        }
        response = self.client.post("/api/push/subscribe", json=payload)
        self.assertEqual(response.status_code, 200)
        subscriptions = list_push_subscriptions(self.user_id)
        self.assertEqual(len(subscriptions), 1)
        self.assertEqual(subscriptions[0]["endpoint"], payload["endpoint"])

        response = self.client.post("/api/push/unsubscribe", json={"endpoint": payload["endpoint"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list_push_subscriptions(self.user_id), [])

    def test_push_status_is_scoped_to_current_user(self):
        response = self.client.get("/api/push/status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("enabled", payload)
        self.assertIn("subscription_count", payload)
        self.assertEqual(payload["subscription_count"], 0)

    def test_push_test_returns_accepted_result(self):
        with patch("modules.web_push.send_test_push", return_value={"ok": True, "sent": 1}):
            response = self.client.post("/api/push/test")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["sent"], 1)

    def test_push_test_surfaces_apple_bad_jwt(self):
        with patch(
            "modules.web_push.send_test_push",
            return_value={"ok": False, "sent": 0, "error": "BadJwtToken", "message": "Push-сервис отклонил JWT."},
        ):
            response = self.client.post("/api/push/test")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "BadJwtToken")

    def test_foreground_poll_does_not_steal_reminder_from_push_dispatcher(self):
        reminder = create_reminder(self.user_id, "test", "2099-01-01T10:00:00+00:00")
        with patch("modules.web_push._send_subscription", return_value=True):
            self.client.post(
                "/api/push/subscribe",
                json={
                    "endpoint": "https://web.push.apple.com/Q12345abcdef",
                    "keys": {"p256dh": "A" * 43, "auth": "B" * 22},
                },
            )
            with patch("modules.web_push.claim_due_reminders", return_value=[]):
                response = self.client.get("/api/reminders/due")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["reminders"], [])
        self.assertTrue(reminder["reminder_id"])

    def test_pwa_shell_loads_push_client(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        response.close()
        self.assertIn('<link rel="manifest" href="/manifest.webmanifest"', html)
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
        self.assertIn('personal-secretary-v12-unified-tasks', worker)
        self.assertIn('"/library.js"', worker)
        self.assertIn('"/tasks.js"', worker)
        self.assertIn('"/tasks-unified.js"', worker)
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

    def test_vapid_subject_uses_public_base_url_origin(self):
        with patch.object(web_push, "BASE_URL", "https://planner.example.com/path?q=1"):
            self.assertEqual(web_push._vapid_subject(), "https://planner.example.com")

    def test_vapid_subject_rejects_localhost_fallback(self):
        with patch.object(web_push, "BASE_URL", "http://127.0.0.1:8080"):
            with self.assertRaises(RuntimeError):
                web_push._vapid_subject()


class ReminderDispatcherTests(unittest.TestCase):
    def test_push_test_records_success_and_uses_declarative_payload(self):
        payloads = []

        def fake_send(subscription, payload):
            payloads.append(payload)
            return True

        with patch("modules.web_push.list_push_subscriptions", return_value=[{
            "id": 1,
            "endpoint": "https://web.push.apple.com/Q12345abcdef",
            "p256dh": "A" * 43,
            "auth": "B" * 22,
        }]), patch("modules.web_push._send_subscription", side_effect=fake_send):
            result = web_push.send_test_push(1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["sent"], 1)
        self.assertEqual(payloads[0]["web_push"], 8030)
        self.assertEqual(payloads[0]["notification"]["title"], "Личный секретарь")

    def test_declarative_payload_has_browser_fallback_content(self):
        payload = web_push._declarative_notification_payload(
            title="Тест",
            body="Проверка",
            tag="tag",
            navigate="/",
            data={"reminder_id": 5},
        )
        self.assertEqual(payload["web_push"], 8030)
        self.assertEqual(payload["notification"]["title"], "Тест")
        self.assertEqual(payload["notification"]["body"], "Проверка")
        self.assertEqual(payload["notification"]["navigate"], "/")
        self.assertEqual(payload["notification"]["data"]["reminder_id"], 5)

    def test_expired_subscription_is_removed_and_reminder_released(self):
        reminder = {"id": 101, "user_id": 7, "text": "test"}
        subscription = {"id": 9, "endpoint": "https://web.push.apple.com/Q12345abcdef", "p256dh": "A", "auth": "B"}
        with patch("modules.web_push.claim_due_reminders", return_value=[reminder]), patch(
            "modules.web_push.list_push_subscriptions", return_value=[subscription]
        ), patch("modules.web_push._send_subscription", side_effect=web_push.WebPushTransportError(410, "gone")), patch(
            "modules.web_push.delete_push_subscription"
        ) as delete_subscription, patch("modules.web_push.release_reminder") as release:
            _dispatch_due_reminders()
        delete_subscription.assert_called_once_with(9)
        release.assert_called_once_with(101)


if __name__ == "__main__":
    unittest.main()
