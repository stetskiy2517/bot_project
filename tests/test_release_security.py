from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import socket
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from flask.testing import FlaskClient
import web_app
from core.db import conn, db_lock, get_or_create_google_user
from core.user_operations import (
    UserBusyError, begin_request, create_web_session, current_operation, find_request,
    load_conversation, remember_calendar_effect, revoke_web_session, save_conversation,
    user_operation,
)
from integrations.push_security import (
    InvalidPushEndpoint, SafePushTransport, public_push_addresses,
    validate_push_endpoint, validate_push_keys,
)
from tests.web_client import bind_test_oauth, push_keys, request_key, set_test_session


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.app.config.update(TESTING=True)
        self.app.test_client_class = FlaskClient
        self.client = self.app.test_client()
        key = secrets.token_hex(10)
        self.user = get_or_create_google_user(key, key + "@example.test", "Security test")
        with self.client.session_transaction() as stored:
            set_test_session(stored, self.user)
            self.csrf = stored["csrf_token"]
            self.sid = stored["sid"]

    def headers(self, key=None):
        return {"X-CSRF-Token": self.csrf, "Idempotency-Key": key or request_key()}

    def test_csrf_is_required_and_has_no_side_effect(self):
        for headers in ({}, {"X-CSRF-Token": "wrong"}):
            with self.subTest(headers=headers), patch("web_app.process_web_message") as execute:
                response = self.client.post("/api/chat", json={"message": "hello"}, headers=headers)
                self.assertEqual(response.status_code, 403)
                execute.assert_not_called()

    def test_mutating_poll_requires_csrf(self):
        with patch("web_app.claim_due_for_user") as claim:
            response = self.client.get("/api/reminders/due")
        self.assertEqual(response.status_code, 403)
        claim.assert_not_called()

    def test_cross_origin_and_cross_site_are_rejected(self):
        cases = [
            {"Origin": "https://evil.test"}, {"Origin": "null"},
            {"Origin": "http://sub.localhost"}, {"Sec-Fetch-Site": "cross-site"},
        ]
        for extra in cases:
            with self.subTest(extra=extra), patch("web_app.process_web_message") as execute:
                response = self.client.post(
                    "/api/chat", json={"message": "hello"}, headers={**self.headers(), **extra},
                )
                self.assertEqual(response.status_code, 403)
                execute.assert_not_called()

    def test_sensitive_reads_are_not_cacheable(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("Cookie", response.headers["Vary"])
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.get_json()["csrf_token"], self.csrf)

    def test_revoked_session_does_not_reauthenticate_from_user_id(self):
        revoke_web_session(self.sid)
        self.assertEqual(self.client.get("/api/status").status_code, 401)
        with self.client.session_transaction() as stored:
            self.assertNotIn("user_id", stored)

    def test_active_session_refresh_does_not_restore_expired_session(self):
        from core.user_operations import session_hash, valid_web_session
        with db_lock:
            conn.execute("UPDATE web_sessions SET expires_at=? WHERE session_hash=?",
                         (time.time()+60, session_hash(self.sid)))
            conn.commit()
        self.assertTrue(valid_web_session(self.user, self.sid))
        with db_lock:
            expiry = conn.execute("SELECT expires_at FROM web_sessions WHERE session_hash=?",
                                  (session_hash(self.sid),)).fetchone()[0]
            self.assertGreater(expiry, time.time()+89*86400)
            conn.execute("UPDATE web_sessions SET expires_at=? WHERE session_hash=?",
                         (time.time()-1, session_hash(self.sid)))
            conn.commit()
        self.assertFalse(valid_web_session(self.user, self.sid))

    def test_logout_revokes_only_its_session(self):
        other = self.app.test_client()
        with other.session_transaction() as stored:
            set_test_session(stored, self.user)
        response = self.client.post("/api/logout", headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/status").status_code, 401)
        self.assertEqual(other.get("/api/status").status_code, 200)

    def test_browser_binding_rejects_other_browser_and_repeated_callback(self):
        a, b = self.app.test_client(), self.app.test_client()
        with patch("web_app.build_web_signin_url", return_value="https://accounts.google.com/auth?state=flow"):
            self.assertEqual(a.get("/api/google/login").status_code, 200)
        with patch("web_app.complete_web_signin", return_value=self.user) as complete:
            self.assertEqual(b.get("/oauth2callback?state=flow&code=code").status_code, 400)
            complete.assert_not_called()
            self.assertEqual(a.get("/oauth2callback?state=flow&code=code").status_code, 302)
            self.assertEqual(a.get("/oauth2callback?state=flow&code=code").status_code, 400)
            complete.assert_called_once()

    def test_browser_binding_expiry_and_state_mismatch(self):
        for state, age in (("wrong", 0), ("flow", 901), ("flow", -60)):
            bind_test_oauth(self.client, "flow")
            with self.client.session_transaction() as stored:
                binding = dict(stored["oauth_browser"])
                binding["created_at"] = time.time() - age
                stored["oauth_browser"] = binding
            with patch("web_app.complete_web_signin") as complete:
                response = self.client.get(f"/oauth2callback?state={state}&code=code")
                self.assertEqual(response.status_code, 400)
                complete.assert_not_called()

    def test_same_key_three_times_executes_once(self):
        key = request_key()
        calls = []
        async def route(update, context, text=None):
            calls.append(text)
            await update.message.reply_text("Saved once")
            return True
        with patch("web_app.route_text", side_effect=route):
            replies = [self.client.post(
                "/api/chat", json={"message": "command"}, headers=self.headers(key),
            ) for _ in range(3)]
        self.assertEqual(calls, ["command"])
        self.assertTrue(all(response.status_code == 200 for response in replies))
        self.assertEqual(replies[0].get_json(), replies[-1].get_json())
        self.assertEqual(replies[-1].headers["Idempotency-Replayed"], "true")

    def test_distinct_keys_with_identical_text_are_distinct_commands(self):
        calls = []
        async def route(update, context, text=None):
            calls.append(text)
            return True
        with patch("web_app.route_text", side_effect=route):
            for _ in range(2):
                self.assertEqual(self.client.post(
                    "/api/chat", json={"message": "same text"}, headers=self.headers(),
                ).status_code, 200)
        self.assertEqual(len(calls), 2)

    def test_key_reuse_with_different_input_is_rejected(self):
        key = request_key()
        self.client.post("/api/chat", json={"message": ""}, headers=self.headers(key))
        response = self.client.post("/api/chat", json={"message": "other"}, headers=self.headers(key))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "request_key_conflict")

    def test_expired_key_never_becomes_new_after_record_cleanup(self):
        key = f"{int((time.time()-86410)*1000)}.{secrets.token_hex(16)}"
        with patch("web_app.route_text") as execute:
            response = self.client.post("/api/chat", json={"message": "hello"}, headers=self.headers(key))
        self.assertEqual(response.status_code, 400)
        execute.assert_not_called()
        self.assertIsNone(find_request(self.user, key))

    def test_request_results_are_owner_scoped(self):
        other_id = get_or_create_google_user(secrets.token_hex(12), "other@example.test", "Other")
        key = request_key()
        begin_request(other_id, key, "private")
        self.assertEqual(self.client.get("/api/requests/" + key).status_code, 404)

    def test_uncertain_failure_does_not_execute_again(self):
        key = request_key()
        with patch("web_app.process_web_message", side_effect=RuntimeError("failed")) as execute:
            first = self.client.post("/api/chat", json={"message": "hello"}, headers=self.headers(key))
            second = self.client.post("/api/chat", json={"message": "hello"}, headers=self.headers(key))
        self.assertEqual(first.status_code, 500)
        self.assertEqual(second.status_code, 409)
        self.assertTrue(second.get_json()["check_only"])
        execute.assert_called_once()

    def test_calendar_unknown_result_is_reconciled_by_id_not_reinserted(self):
        key = request_key()
        begin_request(self.user, key, "fingerprint")
        token = current_operation.set({"user_id": self.user, "key": key})
        try:
            event = remember_calendar_effect(self.user, {"summary": "Test"})
        finally:
            current_operation.reset(token)
        service = MagicMock()
        service.events.return_value.get.return_value.execute.return_value = event
        with patch("modules.calendar_user._get_calendar_service", return_value=service):
            result = self.client.get("/api/requests/" + key)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.get_json()["recovered"])
        service.events.return_value.insert.assert_not_called()
        service.events.return_value.get.assert_called_once_with(calendarId="primary", eventId=event["id"])
        self.assertTrue(find_request(self.user, key)["effect"]["confirmed"])

    def test_state_survives_memory_reset_with_datetime_values(self):
        value = {"smart_planner_pending": {"type": "free_slot_choice",
                 "slots": [(datetime(2030, 2, 3, tzinfo=timezone.utc),
                            datetime(2030, 2, 3, 1, tzinfo=timezone.utc))]}}
        save_conversation(self.user, value)
        web_app._user_state.clear()
        restored = load_conversation(self.user)
        self.assertEqual(restored["smart_planner_pending"]["slots"][0][0],
                         value["smart_planner_pending"]["slots"][0][0])

    def test_expired_state_does_not_reappear(self):
        save_conversation(self.user, {"private": "state"})
        with db_lock:
            conn.execute("UPDATE conversation_states SET expires_at=? WHERE user_id=?", (time.time()-1,self.user))
            conn.commit()
        self.assertEqual(load_conversation(self.user), {})

    def test_user_guard_is_thread_and_process_safe(self):
        failures = []
        with user_operation(self.user):
            def acquire():
                try:
                    with user_operation(self.user, timeout=0):
                        failures.append("unexpected acquisition")
                except UserBusyError:
                    pass
            thread = threading.Thread(target=acquire)
            thread.start(); thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            script = (
                "from core.user_operations import user_operation,UserBusyError\n"
                f"try:\n with user_operation({self.user},timeout=0): raise AssertionError('lock bypass')\n"
                "except UserBusyError: pass\n"
            )
            result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_public_server_rejects_weak_signing_secret(self):
        with patch.object(web_app, "BASE_URL", "https://assistant.example.test"), \
             patch.object(web_app, "WEB_SESSION_SECRET", "dev-only-change-me"):
            with self.assertRaises(RuntimeError):
                web_app.create_web_app()


class PushSecurityTests(unittest.TestCase):
    def test_supported_provider_endpoints_and_keys(self):
        valid = [
            "https://fcm.googleapis.com/fcm/send/example",
            "https://updates.push.services.mozilla.com/wpush/v2/example",
            "https://web.push.apple.com/Qexample",
            "https://wns2-par02p.notify.windows.com/?token=sample",
        ]
        # Windows push URLs normally have a concrete path.
        for value in valid:
            with self.subTest(value=value):
                self.assertTrue(validate_push_endpoint(value))
        keys = push_keys()
        validate_push_keys(keys["p256dh"], keys["auth"])

    def test_arbitrary_hosts_userinfo_ports_and_parser_tricks_rejected(self):
        invalid = [
            "http://fcm.googleapis.com/fcm/send/x",
            "https://127.0.0.1/x", "https://[::1]/x",
            "https://169.254.169.254/latest/meta-data/",
            "https://fcm.googleapis.com.evil.test/fcm/send/x",
            "https://fcm.googleapis.com@evil.test/x",
            "https://evil.test@fcm.googleapis.com/fcm/send/x",
            "https://fcm.googleapis.com:8443/fcm/send/x",
            "https://fcm.googleapis.com/fcm/send/x#fragment",
            "https://fcm.googleapis.com\\@evil.test/x",
            "https://fcm.googleapis.com\n/fcm/send/x",
            "https://evilnotify.windows.com/x",
            "https://fcm.googleapis.com/arbitrary-service",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(InvalidPushEndpoint):
                validate_push_endpoint(value)

    def test_keys_must_be_valid_base64_and_curve_point(self):
        keys = push_keys()
        for first, second in (("bad",keys["auth"]), (keys["p256dh"],"bad"), (keys["p256dh"],"="*16)):
            with self.subTest(first=first), self.assertRaises(ValueError):
                validate_push_keys(first,second)

    def test_all_dns_answers_must_be_public(self):
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "192.0.2.1"):
            answers=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443)),
                     (socket.AF_INET,socket.SOCK_STREAM,6,"",(address,443))]
            with self.subTest(address=address), patch("socket.getaddrinfo",return_value=answers), \
                 self.assertRaises(InvalidPushEndpoint):
                public_push_addresses("fcm.googleapis.com")

    def test_transport_pins_ip_preserves_tls_host_and_disables_redirects(self):
        pool=MagicMock()
        raw=pool.urlopen.return_value
        raw.status=201; raw.reason="Created"; raw.headers={}; raw.read.return_value=b"ok"
        with patch("integrations.push_security.public_push_addresses",return_value=["8.8.8.8"]) as dns, \
             patch("integrations.push_security.urllib3.HTTPSConnectionPool") as factory:
            factory.return_value.__enter__.return_value=pool
            response=SafePushTransport().post(
                "https://fcm.googleapis.com/fcm/send/x",data=b"encrypted",headers={},timeout=10,
            )
        self.assertEqual(response.status_code,201)
        dns.assert_called_once_with("fcm.googleapis.com")
        self.assertEqual(factory.call_args.args[0],"8.8.8.8")
        self.assertEqual(factory.call_args.kwargs["server_hostname"],"fcm.googleapis.com")
        self.assertEqual(factory.call_args.kwargs["assert_hostname"],"fcm.googleapis.com")
        self.assertFalse(pool.urlopen.call_args.kwargs["redirect"])
        self.assertFalse(pool.urlopen.call_args.kwargs["retries"])

    def test_redirect_is_rejected_without_following_location(self):
        pool=MagicMock()
        pool.urlopen.return_value.status=302
        with patch("integrations.push_security.public_push_addresses",return_value=["8.8.8.8"]), \
             patch("integrations.push_security.urllib3.HTTPSConnectionPool") as factory:
            factory.return_value.__enter__.return_value=pool
            with self.assertRaises(InvalidPushEndpoint):
                SafePushTransport().post("https://fcm.googleapis.com/fcm/send/x",data=b"x",headers={},timeout=10)
        pool.urlopen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
