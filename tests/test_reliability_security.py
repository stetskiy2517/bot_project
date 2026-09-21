import hashlib
import io
import json
import subprocess
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import web_app
from core import command_store
from core.db import get_or_create_google_user, save_oauth_state, consume_oauth_state
from core.note_store import create_note, list_notes
from integrations.push_policy import TrustedPushSession, validate_push_endpoint, validate_push_keys
from tests.web_test_support import request_id, push_keys


class RequestSecurityTests(unittest.TestCase):
    def setUp(self):
        with patch.object(web_app, "start_reminder_push_worker"):
            self.app = web_app.create_web_app()
        self.client = self.app.test_client()
        suffix = uuid.uuid4().hex
        self.user_id = get_or_create_google_user(suffix, suffix + "@example.test", "Test")
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id
        self.token = self.client.get("/api/status").get_json()["csrf_token"]

    def headers(self, key=None):
        return {"X-CSRF-Token": self.token, "X-Request-ID": key or request_id()}

    def test_missing_csrf_and_request_ids_are_rejected(self):
        with patch.object(web_app, "route_text", new=AsyncMock()) as route:
            self.assertEqual(self.client.post("/api/chat", json={"message": "test"}).status_code, 403)
            response = self.client.post("/api/chat", json={"message": "test"}, headers={"X-CSRF-Token": self.token})
            self.assertEqual(response.status_code, 428)
            route.assert_not_awaited()

    def test_cross_site_request_cannot_mutate(self):
        headers = {**self.headers(), "Origin": "https://outside.example", "Sec-Fetch-Site": "cross-site"}
        with patch.object(web_app, "route_text", new=AsyncMock()) as route:
            self.assertEqual(self.client.post("/api/chat", json={"message": "test"}, headers=headers).status_code, 403)
            route.assert_not_awaited()

    def test_replayed_text_creates_one_note_and_returns_same_result(self):
        async def route(update, context, text=None):
            note = create_note(update.effective_user.id, text)
            await update.message.reply_text(str(note["note_id"]))
            return True
        key = request_id()
        with patch.object(web_app, "route_text", side_effect=route) as dispatch:
            responses = [self.client.post("/api/chat", json={"message": "bread"}, headers=self.headers(key)) for _ in range(3)]
            self.assertEqual(dispatch.call_count, 1)
        self.assertEqual([r.status_code for r in responses], [200, 200, 200])
        self.assertEqual(responses[0].get_json(), responses[2].get_json())
        self.assertEqual(len(list_notes(self.user_id)), 1)

    def test_same_key_with_changed_payload_is_rejected(self):
        with patch.object(web_app, "route_text", new=AsyncMock(return_value=True)) as route:
            key = request_id()
            self.client.post("/api/chat", json={"message": "one"}, headers=self.headers(key))
            response = self.client.post("/api/chat", json={"message": "two"}, headers=self.headers(key))
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.get_json()["error"], "request_id_conflict")
            self.assertEqual(route.await_count, 1)

    def test_identical_text_with_distinct_ids_is_not_merged(self):
        with patch.object(web_app, "route_text", new=AsyncMock(return_value=True)) as route:
            for _ in range(2):
                response = self.client.post("/api/chat", json={"message": "one"}, headers=self.headers())
                self.assertEqual(response.status_code, 200)
            self.assertEqual(route.await_count, 2)

    def test_another_users_result_is_not_replayed(self):
        key = request_id()
        with patch.object(web_app, "route_text", new=AsyncMock(return_value=True)) as route:
            self.client.post("/api/chat", json={"message": "same"}, headers=self.headers(key))
            second = self.app.test_client()
            uid = get_or_create_google_user(uuid.uuid4().hex, "other@example.test", "Other")
            with second.session_transaction() as stored:
                stored["user_id"] = uid
            token = second.get("/api/status").get_json()["csrf_token"]
            response = second.post("/api/chat", json={"message": "same"}, headers={"X-CSRF-Token": token, "X-Request-ID": key})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(route.await_count, 2)

    def test_voice_retry_ignores_random_multipart_boundary(self):
        key = request_id()
        with patch.object(web_app, "transcribe_audio", return_value="voice") as transcribe, patch.object(web_app, "route_text", new=AsyncMock(return_value=True)) as route:
            for _ in range(2):
                response = self.client.post("/api/voice", headers=self.headers(key), data={
                    "audio": (io.BytesIO(b"synthetic-audio"), "voice.webm", "audio/webm"), "duration_ms": "900",
                })
                self.assertEqual(response.status_code, 200)
            self.assertEqual(transcribe.call_count, 1)
            self.assertEqual(route.await_count, 1)

    def test_transcription_failure_can_be_retried_without_reexecuting_a_command(self):
        key = request_id()
        with patch.object(web_app, "transcribe_audio", side_effect=[TimeoutError(), "voice"]) as transcribe, patch.object(web_app, "route_text", new=AsyncMock(return_value=True)) as route:
            responses = [self.client.post("/api/voice", headers=self.headers(key), data={
                "audio": (io.BytesIO(b"synthetic-audio"), "voice.webm", "audio/webm"), "duration_ms": "900",
            }) for _ in range(2)]
        self.assertEqual([r.status_code for r in responses], [503, 200])
        self.assertEqual(transcribe.call_count, 2)
        self.assertEqual(route.await_count, 1)

    def test_exception_after_side_effect_never_reexecutes_it(self):
        async def route(update, context, text=None):
            create_note(self.user_id, "saved before connection loss")
            raise RuntimeError("simulated process failure")
        key = request_id()
        with patch.object(web_app, "route_text", side_effect=route) as dispatch:
            for _ in range(2):
                response = self.client.post("/api/chat", json={"message": "one"}, headers=self.headers(key))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json()["error"], "outcome_unknown")
            self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(len(list_notes(self.user_id)), 1)

    def test_pending_context_survives_memory_reset(self):
        seen = []
        stamp = datetime.now(timezone.utc)
        async def route(update, context, text=None):
            if text == "first":
                context.user_data["pending_test"] = {"at": stamp}
            else:
                seen.append(context.user_data.get("pending_test"))
            return True
        with patch.object(web_app, "route_text", side_effect=route):
            self.client.post("/api/chat", json={"message": "first"}, headers=self.headers())
            web_app._user_state.clear()
            self.client.post("/api/chat", json={"message": "second"}, headers=self.headers())
        self.assertEqual(seen, [{"at": stamp}])

    def test_concurrent_commands_for_one_user_do_not_overlap(self):
        entered = threading.Event()
        release = threading.Event()
        async def route(update, context, text=None):
            entered.set()
            release.wait(3)
            return True
        second = self.app.test_client()
        with second.session_transaction() as stored:
            stored["user_id"] = self.user_id
            stored["csrf_token"] = self.token
        first_result = []
        with patch.object(web_app, "route_text", side_effect=route) as dispatch:
            thread = threading.Thread(target=lambda: first_result.append(
                second.post("/api/chat", json={"message": "slow"}, headers=self.headers())
            ))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                response = self.client.post("/api/chat", json={"message": "other"}, headers=self.headers())
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json()["error"], "user_busy")
            finally:
                release.set()
                thread.join(4)
            self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(first_result[0].status_code, 200)

    def test_status_is_private_and_not_cacheable(self):
        response = self.client.get("/api/status")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")


    def test_google_timeout_readback_uses_same_id_without_second_insert(self):
        from modules import calendar
        event = {"summary": "Test", "start": {"dateTime": "2026-12-01T11:00:00+03:00"},
                 "end": {"dateTime": "2026-12-01T12:00:00+03:00"}}
        service = Mock()
        service.events().insert().execute.side_effect = TimeoutError("lost reply")
        service.events().get().execute.side_effect = lambda: dict(event)
        async def route(update, context, text=None):
            calendar._create_event(self.user_id, event)
            return True
        key = request_id()
        with patch.object(web_app, "route_text", side_effect=route), patch.object(calendar, "get_google_token", return_value={**{"token": "test"}}), patch.object(calendar.Credentials, "from_authorized_user_info"), patch.object(calendar, "build", return_value=service), patch("modules.navigation.safe_create_travel_for_event"):
            service.events().insert.reset_mock()
            service.events().get.reset_mock()
            first = self.client.post("/api/chat", json={"message": "meeting"}, headers=self.headers(key))
            replay = self.client.post("/api/chat", json={"message": "meeting"}, headers=self.headers(key))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.get_json(), first.get_json())
        self.assertEqual(service.events().insert.call_count, 1)
        self.assertTrue(event["id"].startswith("sp"))
        self.assertEqual(service.events().get.call_args.kwargs["eventId"], event["id"])

    def test_google_unknown_outcome_is_reconciled_after_memory_reset(self):
        from modules import calendar
        event = {"summary": "Test", "start": {"dateTime": "2026-12-01T11:00:00+03:00"},
                 "end": {"dateTime": "2026-12-01T12:00:00+03:00"}}
        service = Mock()
        service.events().insert().execute.side_effect = TimeoutError("lost reply")
        service.events().get().execute.side_effect = TimeoutError("lookup unavailable")
        async def route(update, context, text=None):
            calendar._create_event(self.user_id, event)
            return True
        key = request_id()
        with patch.object(web_app, "route_text", side_effect=route) as dispatch, patch.object(calendar, "get_google_token", return_value={"token": "test"}), patch.object(calendar.Credentials, "from_authorized_user_info"), patch.object(calendar, "build", return_value=service), patch("modules.calendar_user._get_calendar_service", return_value=service):
            service.events().insert.reset_mock()
            first = self.client.post("/api/chat", json={"message": "meeting"}, headers=self.headers(key))
            self.assertEqual(first.status_code, 409)
            self.assertEqual(first.headers["X-Frame-Options"], "DENY")
            web_app._user_state.clear()
            service.events().get().execute.side_effect = lambda: dict(event)
            recovered = self.client.post("/api/chat", json={"message": "meeting"}, headers=self.headers(key))
        self.assertEqual(recovered.status_code, 200)
        self.assertTrue(recovered.get_json()["recovered"])
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(service.events().insert.call_count, 1)

    def test_google_recovery_does_not_claim_an_unrelated_event(self):
        service = Mock()
        service.events().get().execute.return_value = {
            "id": "unrelated", "summary": "Not this request", "extendedProperties": {"private": {}},
        }
        async def route(update, context, text=None):
            command_store.prepare_calendar_create(self.user_id, {"summary": "Expected"})
            raise TimeoutError()
        key = request_id()
        with patch.object(web_app, "route_text", side_effect=route) as dispatch, patch("modules.calendar_user._get_calendar_service", return_value=service):
            self.client.post("/api/chat", json={"message": "meeting"}, headers=self.headers(key))
            response = self.client.post("/api/chat", json={"message": "meeting"}, headers=self.headers(key))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "outcome_unknown")
        self.assertEqual(dispatch.call_count, 1)


class OAuthBindingTests(unittest.TestCase):
    def setUp(self):
        with patch.object(web_app, "start_reminder_push_worker"):
            self.app = web_app.create_web_app()
        self.a = self.app.test_client()
        self.b = self.app.test_client()
        self.state = uuid.uuid4().hex
        self.uid = get_or_create_google_user(uuid.uuid4().hex, "oauth-bind@example.test", "OAuth")

    def start(self):
        with patch.object(web_app, "build_web_signin_url", return_value=f"https://accounts.google.com/auth?state={self.state}"):
            self.assertEqual(self.a.get("/api/google/login").status_code, 200)

    def test_callback_requires_initiating_browser_and_is_single_use(self):
        self.start()
        query = {"state": self.state, "code": "test-code"}
        with patch.object(web_app, "complete_web_signin", return_value=self.uid) as exchange:
            self.assertEqual(self.b.get("/oauth2callback", query_string=query).status_code, 400)
            self.assertEqual(self.a.get("/oauth2callback", query_string=query).status_code, 302)
            self.assertEqual(self.a.get("/oauth2callback", query_string=query).status_code, 400)
            exchange.assert_called_once_with(self.state, "test-code")

    def test_expired_binding_cannot_exchange_code(self):
        self.start()
        with self.a.session_transaction() as stored:
            binding = dict(stored["oauth_binding"])
            binding["issued_at"] = time.time() - 901
            stored["oauth_binding"] = binding
        with patch.object(web_app, "complete_web_signin") as exchange:
            self.assertEqual(self.a.get("/oauth2callback", query_string={"state": self.state, "code": "code"}).status_code, 400)
            exchange.assert_not_called()

    def test_server_state_is_consumed_once(self):
        save_oauth_state(self.state)
        self.assertEqual(consume_oauth_state(self.state), 0)
        self.assertIsNone(consume_oauth_state(self.state))

    def test_production_rejects_default_session_secret(self):
        with patch.object(web_app, "BASE_URL", "https://example.test"), patch.object(web_app, "WEB_SESSION_SECRET", "dev-only-change-me"):
            with self.assertRaises(RuntimeError):
                web_app.create_web_app()


class CommandStoreTests(unittest.TestCase):
    def test_expired_and_future_request_ids_are_rejected(self):
        for stamp in (time.time() - 86410, time.time() + 600):
            with self.assertRaises(ValueError):
                command_store.validate_request_id(f"{int(stamp * 1000)}-{uuid.uuid4().hex}")

    def test_user_lock_is_cross_process(self):
        uid = 888100
        code = "from core.command_store import user_operation,UserBusyError\ntry:\n with user_operation(888100,blocking=False): print('acquired')\nexcept UserBusyError: print('busy')"
        with command_store.user_operation(uid):
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "busy")


class PushPolicyTests(unittest.TestCase):
    def test_temporary_dns_failure_does_not_invalidate_subscription(self):
        import requests
        from core.push_store import save_push_subscription, list_push_subscriptions
        from modules.reminder_dispatcher import _deliver_payload
        uid = get_or_create_google_user(uuid.uuid4().hex, "dns@example.test", "DNS")
        keys = push_keys()
        endpoint = "https://fcm.googleapis.com/dns-" + uuid.uuid4().hex
        save_push_subscription(uid, endpoint, **keys)
        with patch("integrations.push_policy.socket.getaddrinfo", side_effect=OSError("temporary")):
            with self.assertRaises(requests.ConnectionError):
                validate_push_endpoint(endpoint, resolve=True)
        with patch("modules.reminder_dispatcher.send_web_push", side_effect=requests.ConnectionError("temporary")):
            result = _deliver_payload(uid, {})
        self.assertEqual(result["removed"], 0)
        self.assertEqual(len(list_push_subscriptions(uid)), 1)

    def test_only_supported_https_hosts_are_accepted(self):
        for endpoint in (
            "http://fcm.googleapis.com/push", "https://127.0.0.1/push",
            "https://[::1]/push", "https://169.254.169.254/latest",
            "https://fcm.googleapis.com.evil.example/push", "https://evil.example/push",
            "https://user:password@fcm.googleapis.com/push", "https://fcm.googleapis.com:444/push",
            "https://fcm.googleapis.com/push#fragment", "https://fcm.googleapis.com\\@evil.example/push",
            "https://fcm.googleapis.com/\npush",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_push_endpoint(endpoint)
        for endpoint in (
            "https://fcm.googleapis.com/fcm/send/key",
            "https://updates.push.services.mozilla.com/wpush/v2/key",
            "https://web.push.apple.com/key",
            "https://wns2-by3p.notify.windows.com/key",
        ):
            self.assertEqual(validate_push_endpoint(endpoint), endpoint)

    def test_dns_private_addresses_are_rejected_before_http(self):
        with patch("integrations.push_policy.socket.getaddrinfo", return_value=[(2,1,6,"",("10.1.2.3",443))]):
            with self.assertRaises(ValueError):
                validate_push_endpoint("https://fcm.googleapis.com/push", resolve=True)

    def test_valid_point_and_auth_key_are_required(self):
        keys = push_keys()
        validate_push_keys(**keys)
        for bad in ("x", "invalid!key", "", "A" * 200):
            with self.assertRaises(ValueError):
                validate_push_keys(keys["p256dh"], bad)

    def test_transport_never_follows_redirects_or_environment_proxies(self):
        import requests
        request = requests.Request("POST", "https://fcm.googleapis.com/push").prepare()
        with TrustedPushSession() as http:
            self.assertFalse(http.trust_env)
            with patch("integrations.push_policy.socket.getaddrinfo", return_value=[(2,1,6,"",("8.8.8.8",443))]), patch("requests.Session.send", return_value=Mock(status_code=302)) as send:
                http.send(request, allow_redirects=True)
                self.assertFalse(send.call_args.kwargs["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
