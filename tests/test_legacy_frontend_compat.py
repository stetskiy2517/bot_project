import io
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import web_app
from core.db import get_or_create_google_user
from core.note_store import create_note
from core.reminder_store import create_reminder


class LegacyFrontendCompatibilityTests(unittest.TestCase):
    def setUp(self):
        with patch.object(web_app, "start_reminder_push_worker"), patch.object(
            web_app, "start_navigation_monitor_worker"
        ):
            self.app = web_app.create_web_app()
        self.client = self.app.test_client()
        suffix = uuid.uuid4().hex
        self.user_id = get_or_create_google_user(
            suffix, f"legacy-{suffix}@example.test", "Legacy Client"
        )
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id
        self.legacy_headers = {
            "Origin": "http://localhost",
            "Sec-Fetch-Site": "same-origin",
        }

    def test_rendered_page_loads_reliability_before_inline_api_code(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        marker = '<script src="/reliability.js"></script>'
        self.assertIn(marker, html)
        self.assertIn("PlannerRequests.request", html)
        self.assertLess(html.index(marker), html.index("PlannerRequests.request"))
        self.assertEqual(self.client.get("/reliability.js").status_code, 200)

    def test_old_open_tab_can_still_send_chat_without_new_headers(self):
        with patch.object(web_app, "route_text", new=AsyncMock(return_value=True)) as route:
            response = self.client.post(
                "/api/chat",
                json={"message": "проверка старой вкладки"},
                headers=self.legacy_headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Legacy-Client"), "true")
        self.assertEqual(response.headers.get("X-Client-Upgrade"), "reload")
        self.assertTrue(response.get_json().get("request_id"))
        route.assert_awaited_once()

    def test_old_open_tab_can_open_note_and_reminder(self):
        note = create_note(self.user_id, "legacy note")
        reminder = create_reminder(
            self.user_id,
            "legacy reminder",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        note_response = self.client.post(
            "/api/library/open",
            json={"type": "note", "id": note["note_id"]},
            headers=self.legacy_headers,
        )
        reminder_response = self.client.post(
            "/api/library/open",
            json={"type": "reminder", "id": reminder["reminder_id"]},
            headers=self.legacy_headers,
        )
        self.assertEqual(note_response.status_code, 200)
        self.assertEqual(note_response.get_json()["type"], "note")
        self.assertEqual(reminder_response.status_code, 200)
        self.assertEqual(reminder_response.get_json()["type"], "reminder")

    def test_old_open_tab_can_send_voice(self):
        with patch.object(web_app, "transcribe_audio", return_value="голос"), patch.object(
            web_app, "route_text", new=AsyncMock(return_value=True)
        ) as route:
            response = self.client.post(
                "/api/voice",
                data={
                    "audio": (io.BytesIO(b"synthetic-audio"), "voice.webm", "audio/webm"),
                    "duration_ms": "900",
                },
                headers=self.legacy_headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["transcript"], "голос")
        route.assert_awaited_once()

    def test_cross_site_or_unverifiable_legacy_write_is_still_rejected(self):
        with patch.object(web_app, "route_text", new=AsyncMock()) as route:
            cross_site = self.client.post(
                "/api/chat",
                json={"message": "cross site"},
                headers={
                    "Origin": "https://outside.example",
                    "Sec-Fetch-Site": "cross-site",
                },
            )
            no_browser_proof = self.client.post(
                "/api/chat", json={"message": "no provenance"}
            )
        self.assertEqual(cross_site.status_code, 403)
        self.assertEqual(no_browser_proof.status_code, 403)
        route.assert_not_awaited()

    def test_partially_upgraded_client_does_not_bypass_request_id_requirement(self):
        token = self.client.get("/api/status").get_json()["csrf_token"]
        with patch.object(web_app, "route_text", new=AsyncMock()) as route:
            response = self.client.post(
                "/api/chat",
                json={"message": "missing id"},
                headers={
                    **self.legacy_headers,
                    "X-CSRF-Token": token,
                },
            )
        self.assertEqual(response.status_code, 428)
        route.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
