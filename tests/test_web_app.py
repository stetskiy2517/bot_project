import io
import unittest
from unittest.mock import patch

import web_app
from core.db import get_or_create_google_user


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()
        web_app._user_state.clear()

    def _google_session(self, client, sub, email, name):
        uid = get_or_create_google_user(sub, email, name)
        with client.session_transaction() as session:
            session["user_id"] = uid
        return uid

    def test_health_does_not_require_telegram(self):
        self.assertEqual(
            self.client.get("/api/health").get_json(),
            {"status": "ok", "transport": "web"},
        )

    def test_private_api_requires_google_session(self):
        self.assertEqual(self.client.get("/api/status").status_code, 401)
        self.assertEqual(self.client.post("/api/chat", json={"message": "test"}).status_code, 401)
        self.assertEqual(self.client.post("/api/voice").status_code, 401)

    def test_google_login_is_public(self):
        with patch("web_app.build_web_signin_url", return_value="https://accounts.google.test/auth") as build:
            response = self.client.get("/api/google/login")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["url"], "https://accounts.google.test/auth")
        build.assert_called_once_with()

    def test_two_google_accounts_have_independent_sessions(self):
        a = self.app.test_client()
        b = self.app.test_client()
        aid = self._google_session(a, "sub-a", "a@example.test", "Alice")
        bid = self._google_session(b, "sub-b", "b@example.test", "Bob")
        self.assertNotEqual(aid, bid)
        self.assertEqual(a.get("/api/status").get_json()["user"]["email"], "a@example.test")
        self.assertEqual(b.get("/api/status").get_json()["user"]["email"], "b@example.test")

    def test_settings_are_isolated_between_google_users(self):
        a = self.app.test_client()
        b = self.app.test_client()
        self._google_session(a, "settings-a", "sa@example.test", "A")
        self._google_session(b, "settings-b", "sb@example.test", "B")
        self.assertEqual(a.post("/api/settings", json={"timezone": "Europe/Tallinn", "buffer_minutes": 45}).status_code, 200)
        self.assertEqual(b.post("/api/settings", json={"timezone": "Europe/Moscow", "buffer_minutes": 5}).status_code, 200)
        sa = a.get("/api/status").get_json()
        sb = b.get("/api/status").get_json()
        self.assertEqual(sa["timezone"], "Europe/Tallinn")
        self.assertEqual(sb["timezone"], "Europe/Moscow")
        self.assertEqual(sa["preferences"]["buffer_minutes"], 45)
        self.assertEqual(sb["preferences"]["buffer_minutes"], 5)

    def test_chat_routes_with_google_session_user_id(self):
        a = self.app.test_client()
        b = self.app.test_client()
        aid = self._google_session(a, "chat-a", "ca@example.test", "Alice")
        bid = self._google_session(b, "chat-b", "cb@example.test", "Bob")
        seen = []

        async def fake_route(update, context, text=None):
            seen.append((update.effective_user.id, update.effective_user.full_name, text))
            await update.message.reply_text("ok")
            return True

        with patch("web_app.route_text", side_effect=fake_route):
            a.post("/api/chat", json={"message": "A"})
            b.post("/api/chat", json={"message": "B"})
        self.assertEqual(seen, [(aid, "Alice", "A"), (bid, "Bob", "B")])

    def test_voice_transcribes_normalizes_and_routes_through_same_router(self):
        user_id = self._google_session(self.client, "voice-user", "voice@example.test", "Voice User")
        seen = []

        async def fake_route(update, context, text=None):
            seen.append((update.effective_user.id, text))
            await update.message.reply_text("Готово")
            return True

        with patch("web_app.transcribe_audio", return_value="встреча завтра в 19.30") as transcribe:
            with patch("web_app.route_text", side_effect=fake_route):
                response = self.client.post(
                    "/api/voice",
                    data={"audio": (io.BytesIO(b"fake-audio"), "voice.webm", "audio/webm")},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["transcript"], "встреча завтра в 19:30")
        self.assertEqual(payload["replies"], ["Готово"])
        self.assertEqual(seen, [(user_id, "встреча завтра в 19:30")])
        transcribe.assert_called_once()

    def test_voice_rejects_unsupported_upload(self):
        self._google_session(self.client, "voice-format", "format@example.test", "Format")
        response = self.client.post(
            "/api/voice",
            data={"audio": (io.BytesIO(b"not-audio"), "note.txt", "text/plain")},
        )
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.get_json()["error"], "unsupported_audio")

    def test_voice_rejects_mismatched_mime_even_with_audio_extension(self):
        self._google_session(self.client, "voice-mismatch", "mismatch@example.test", "Mismatch")
        response = self.client.post(
            "/api/voice",
            data={"audio": (io.BytesIO(b"not-audio"), "voice.webm", "text/plain")},
        )
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.get_json()["error"], "unsupported_audio")

    def test_voice_rejects_empty_transcript(self):
        self._google_session(self.client, "voice-empty", "empty@example.test", "Empty")
        with patch("web_app.transcribe_audio", return_value=""):
            response = self.client.post(
                "/api/voice",
                data={"audio": (io.BytesIO(b"fake-audio"), "voice.ogg", "audio/ogg")},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "empty_transcript")

    def test_voice_provider_failure_is_user_safe(self):
        self._google_session(self.client, "voice-fail", "fail@example.test", "Fail")
        with patch("web_app.transcribe_audio", side_effect=RuntimeError("provider secret detail")):
            response = self.client.post(
                "/api/voice",
                data={"audio": (io.BytesIO(b"fake-audio"), "voice.webm", "audio/webm")},
            )
        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload["error"], "voice_failed")
        self.assertNotIn("provider secret detail", str(payload))

    def test_voice_request_size_is_limited(self):
        self._google_session(self.client, "voice-large", "large@example.test", "Large")
        self.app.config["MAX_CONTENT_LENGTH"] = 64
        response = self.client.post(
            "/api/voice",
            data={"audio": (io.BytesIO(b"x" * 256), "voice.webm", "audio/webm")},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["error"], "audio_too_large")

    def test_logout_removes_google_session(self):
        self._google_session(self.client, "logout-sub", "logout@example.test", "Logout")
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        self.client.post("/api/logout")
        self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_pwa_shell_contains_voice_and_calendar_controls(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        response.close()
        for control in [
            "settingsBtn",
            "timezone",
            "workStart",
            "workEnd",
            "days",
            "buffer",
            "saveSettings",
            "voiceBtn",
        ]:
            self.assertIn(f'id="{control}"', html)
        self.assertIn("MediaRecorder", html)
        self.assertIn("/api/voice", html)
        self.assertIn("Войти через Google", html)
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        manifest.close()


if __name__ == "__main__":
    unittest.main()
