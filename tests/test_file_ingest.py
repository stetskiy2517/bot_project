from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from modules import file_ingest


NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


class FileIngestTests(unittest.TestCase):
    def test_extracts_fenced_json(self):
        result = file_ingest._extract_json_object('```json\n{"events": []}\n```')
        self.assertEqual(result, {"events": []})

    def test_flight_ticket_keeps_local_airport_timezones(self):
        answer = json.dumps({
            "document_type": "flight_ticket",
            "summary": "Рейс Таллин — Хельсинки",
            "events": [{
                "title": "Рейс AY101 Таллин — Хельсинки",
                "start_local": "2026-10-01T10:00",
                "end_local": "2026-10-01T11:30",
                "start_timezone": "Europe/Tallinn",
                "end_timezone": "Europe/Helsinki",
                "location": "TLL → HEL",
                "description": "Рейс AY101",
                "category": "travel",
                "confidence": 0.96,
            }],
            "warnings": [],
        })
        with patch("modules.file_ingest.upload_file_bytes", return_value="file-1") as upload, \
             patch("modules.file_ingest.complete_with_file", return_value=answer), \
             patch("modules.file_ingest.delete_file") as delete:
            result = file_ingest.analyze_file_bytes(
                b"ticket",
                filename="document.pdf",
                mimetype="application/pdf",
                user_timezone="Europe/Moscow",
                now=NOW,
            )
        upload.assert_called_once()
        delete.assert_called_once_with("file-1")
        self.assertTrue(result["temporary_file_deleted"])
        event = result["events"][0]
        self.assertTrue(event["ready"])
        self.assertEqual(event["category"], "travel")
        self.assertEqual(event["start_timezone"], "Europe/Tallinn")
        self.assertEqual(event["end_timezone"], "Europe/Helsinki")
        self.assertTrue(event["start"].endswith("+03:00"))
        self.assertTrue(event["end"].endswith("+03:00"))

    def test_travel_event_without_timezone_requires_manual_review(self):
        event = file_ingest._normalize_event(
            {
                "title": "Рейс неизвестного аэропорта",
                "start_local": "2026-10-01T10:00",
                "end_local": "2026-10-01T12:00",
                "start_timezone": None,
                "end_timezone": None,
                "confidence": 0.9,
            },
            document_type="flight_ticket",
            user_timezone="Europe/Moscow",
            now=NOW,
        )
        self.assertIsNotNone(event)
        self.assertFalse(event["ready"])
        self.assertIsNone(event["start"])
        self.assertTrue(any("часовой пояс" in warning for warning in event["warnings"]))

    def test_nontravel_event_can_use_account_timezone(self):
        event = file_ingest._normalize_event(
            {
                "title": "Приём у врача",
                "start_local": "2026-10-02T14:00",
                "end_local": "2026-10-02T15:00",
                "confidence": 0.92,
                "category": "health",
            },
            document_type="appointment",
            user_timezone="Europe/Moscow",
            now=NOW,
        )
        self.assertIsNotNone(event)
        self.assertTrue(event["ready"])
        self.assertEqual(event["start_timezone"], "Europe/Moscow")
        self.assertTrue(any("часовой пояс аккаунта" in warning for warning in event["warnings"]))

    def test_provider_file_is_deleted_when_analysis_fails(self):
        with patch("modules.file_ingest.upload_file_bytes", return_value="file-2"), \
             patch("modules.file_ingest.complete_with_file", side_effect=RuntimeError("provider failed")), \
             patch("modules.file_ingest.delete_file") as delete:
            with self.assertRaises(RuntimeError):
                file_ingest.analyze_file_bytes(
                    b"ticket",
                    filename="document.pdf",
                    mimetype="application/pdf",
                    user_timezone="Europe/Moscow",
                    now=NOW,
                )
        delete.assert_called_once_with("file-2")

    def test_failed_provider_delete_is_reported_not_claimed(self):
        answer = json.dumps({"document_type": "other", "summary": "Документ", "events": [], "warnings": []})
        with patch("modules.file_ingest.upload_file_bytes", return_value="file-3"), \
             patch("modules.file_ingest.complete_with_file", return_value=answer), \
             patch("modules.file_ingest.delete_file", side_effect=RuntimeError("delete failed")):
            result = file_ingest.analyze_file_bytes(
                b"document",
                filename="document.pdf",
                mimetype="application/pdf",
                user_timezone="Europe/Moscow",
                now=NOW,
            )
        self.assertFalse(result["temporary_file_deleted"])


if __name__ == "__main__":
    unittest.main()
