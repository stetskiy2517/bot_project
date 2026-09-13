from __future__ import annotations

from datetime import datetime
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import web_app
from integrations import speech
from modules import calendar, router


class SpeechInputAuditTests(unittest.TestCase):
    def test_dates_and_non_time_numbers_survive_transcription_normalization(self):
        samples = (
            "встреча 15.09 в 19:30",
            "встреча 15.09.2026 в 19:30",
            "заметка: стоимость 12.50 рублей",
            "заметка: версия 1.10.2",
            "заметка: адрес 192.168.1.10",
            "встреча на 01.12 в 10:00",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(speech.normalize_time_format(text), text)

    def test_explicit_dotted_time_is_still_normalized(self):
        self.assertEqual(speech.normalize_time_format("встреча 15.09 в 19.30"), "встреча 15.09 в 19:30")

    def test_completed_transcription_without_text_is_empty(self):
        response = Mock()
        response.json.return_value = {"status": "completed", "text": None}
        with patch.object(speech.requests, "get", return_value=response):
            self.assertEqual(speech._wait_for_transcript("test-transcript"), "")


class CalendarInputAuditTests(unittest.TestCase):
    now = datetime(2026, 9, 13, 9, 0)

    def test_every_valid_clock_minute(self):
        for hour in range(24):
            for minute in range(60):
                with self.subTest(hour=hour, minute=minute):
                    self.assertEqual(calendar._extract_time(f"встреча завтра в {hour:02d}:{minute:02d}"), (hour, minute))

    def test_truncated_clock_does_not_silently_become_a_full_hour(self):
        for clock in ("15:", "15:3", "15:300", "15:777", "15.3", "15-3", "25", "15:99"):
            with self.subTest(clock=clock):
                self.assertIsNone(calendar._parse_datetime(f"встреча завтра в {clock}", self.now))

    def test_impossible_named_dates_do_not_fall_back_to_today(self):
        for date in ("31 февраля", "30 февраля 2027", "31 апреля", "31 июня 2027"):
            with self.subTest(date=date):
                self.assertIsNone(calendar._parse_datetime(f"встреча {date} в 15:00", self.now))

    def test_impossible_range_end_is_rejected(self):
        for clock in ("с 15 до 27", "с 15:00 до 16:99"):
            with self.subTest(clock=clock):
                self.assertIsNone(calendar._parse_datetime(f"встреча завтра {clock}", self.now))

    def test_huge_relative_interval_does_not_crash(self):
        for amount in ("99999999999999999999", "9" * 4400):
            with self.subTest(digits=len(amount)):
                self.assertIsNone(calendar._relative_offset(f"через {amount} дней"))
                self.assertIsNone(calendar._parse_datetime(f"встреча через {amount} дней", self.now))

    def test_common_typos_keep_the_create_intent(self):
        for event in ("встреча", "встеча", "втреча", "созовон"):
            for day in ("завтра", "завтро", "сегодя", "севодня", "пятнцу", "суботу"):
                with self.subTest(event=event, day=day):
                    self.assertEqual(router.detect_intent(f"{event} {day} в 15:00").name, router.INTENT_CREATE)


class PendingInputAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_cancels_waiting_for_time_without_creating_an_event(self):
        context = SimpleNamespace(user_data={"smart_planner_pending": {"type": "create_time", "text": "встреча завтра"}})
        update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
        with patch.object(router, "create_from_text", new=AsyncMock(return_value=True)) as create:
            self.assertTrue(await router._resume_pending(update, context, "Стоп!"))
            create.assert_not_awaited()
        self.assertNotIn("smart_planner_pending", context.user_data)


class WebInputAuditTests(unittest.TestCase):
    def setUp(self):
        with patch.object(web_app, "init_db"), patch.object(web_app, "start_reminder_push_worker"):
            self.app = web_app.create_web_app()
        self.app.config.update(TESTING=True, SECRET_KEY="isolated-audit-test-key")
        self.client = self.app.test_client()
        with self.client.session_transaction() as stored:
            stored["user_id"] = 900001
        self.account = patch.object(web_app, "get_google_account", return_value={"name": "Audit", "email": "audit@example.test"})
        self.account.start()
        self.addCleanup(self.account.stop)

    def test_api_rejects_non_object_json_before_dispatch(self):
        paths = ("/api/chat", "/api/settings", "/api/library/open", "/api/push/subscriptions", "/api/library/reminders/1/complete", "/api/library/reminders/1/reschedule")
        for path in paths:
            for value in ([1], "text", True, 42):
                with self.subTest(path=path, value=value):
                    response = self.client.post(path, json=value)
                    self.assertEqual(response.status_code, 400)
                    self.assertTrue(response.is_json)

    def test_chat_requires_a_string_and_bounded_length(self):
        result = web_app.WebPlannerResult(handled=True, replies=[])
        with patch.object(web_app, "process_web_message", new=AsyncMock(return_value=result)) as process, patch.object(web_app, "_with_due_reminders", side_effect=lambda _id, replies: replies):
            for value in (None, True, 123, ["hello"], {"text": "hello"}, "x" * 10001):
                with self.subTest(kind=type(value).__name__):
                    self.assertEqual(self.client.post("/api/chat", json={"message": value}).status_code, 400)
            process.assert_not_awaited()

    def test_invalid_voice_duration_never_reaches_transcription(self):
        with patch.object(web_app, "transcribe_audio", return_value="") as transcribe:
            for value in ("nan", "NaN", "inf", "-inf", "1e309"):
                with self.subTest(value=value):
                    response = self.client.post("/api/voice", data={"audio": (io.BytesIO(b"test"), "test.webm", "audio/webm"), "duration_ms": value})
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.get_json()["error"], "invalid_audio_duration")
            transcribe.assert_not_called()

    def test_push_keys_must_be_an_object(self):
        for value in ([1], "invalid", 12, True):
            with self.subTest(value=value):
                response = self.client.post("/api/push/subscriptions", json={"endpoint": "https://example.test/push", "keys": value})
                self.assertEqual(response.status_code, 400)

    def test_rejected_settings_do_not_partially_save_timezone(self):
        with patch.object(web_app, "save_user_timezone") as timezone, patch.object(web_app, "save_calendar_preferences") as preferences:
            response = self.client.post("/api/settings", json={"timezone": "Europe/Riga", "work_days": [99]})
            self.assertEqual(response.status_code, 400)
            timezone.assert_not_called()
            preferences.assert_not_called()

    def test_oauth_error_is_not_rendered_as_html(self):
        response = self.client.get("/oauth2callback", query_string={"error": "<script>alert('audit')</script>"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.mimetype, "text/plain")

    def test_oauth_exception_does_not_disclose_internal_details(self):
        with patch.object(web_app, "complete_web_signin", side_effect=RuntimeError("sensitive-internal-value")):
            response = self.client.get("/oauth2callback?state=invalid&code=invalid")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("sensitive-internal-value", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
