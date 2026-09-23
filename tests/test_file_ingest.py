from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from integrations import location_timezone
from modules import file_ingest


NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


class FileIngestTests(unittest.TestCase):
    def tearDown(self):
        location_timezone.resolve_location_timezone.cache_clear()

    def test_extracts_fenced_json(self):
        result = file_ingest._extract_json_object('```json\n{"events": []}\n```')
        self.assertEqual(result, {"events": []})

    def test_generic_location_timezone_resolver_uses_existing_geocoder(self):
        location_timezone.resolve_location_timezone.cache_clear()
        with patch("integrations.location_timezone.NAVIGATION_PROVIDER", "ors"), \
             patch("integrations.location_timezone.ors_configured", return_value=True), \
             patch("integrations.location_timezone.ors_geocode", return_value=(37.62, 55.75)), \
             patch("integrations.location_timezone.dgis_configured", return_value=False), \
             patch("integrations.location_timezone.get_tz", return_value="Europe/Moscow"):
            result = location_timezone.resolve_location_timezone("Москва, Кремль")
        self.assertEqual(result["timezone"], "Europe/Moscow")
        self.assertEqual(result["provider"], "ors")
        self.assertAlmostEqual(result["longitude"], 37.62)
        self.assertAlmostEqual(result["latitude"], 55.75)

    def test_generic_movement_corrects_timezone_without_document_specific_rule(self):
        def resolve(place: str):
            if "Москва" in place:
                return {"timezone": "Europe/Moscow", "provider": "test"}
            if "Саратов" in place:
                return {"timezone": "Europe/Saratov", "provider": "test"}
            return None

        with patch("modules.file_ingest.resolve_location_timezone", side_effect=resolve):
            event = file_ingest._normalize_event(
                {
                    "title": "Поезд Москва — Саратов",
                    "start_local": "2026-09-18T19:30",
                    "end_local": "2026-09-18T22:05",
                    "start_location": "Москва, вокзал",
                    "end_location": "Саратов, вокзал",
                    "start_timezone": "Europe/Moscow",
                    "end_timezone": "Europe/Moscow",
                    "confidence": 0.99,
                },
                document_type="train_ticket",
                user_timezone="Europe/Moscow",
                now=NOW,
            )

        self.assertTrue(event["ready"])
        self.assertTrue(event["movement"])
        self.assertEqual(event["start_timezone"], "Europe/Moscow")
        self.assertEqual(event["end_timezone"], "Europe/Saratov")
        self.assertEqual(event["start"], "2026-09-18T19:30:00+03:00")
        self.assertEqual(event["end"], "2026-09-18T22:05:00+04:00")
        self.assertTrue(any("Саратов" in warning for warning in event["warnings"]))

    def test_travel_with_different_valid_timezones_can_work_without_geocoder(self):
        answer = json.dumps({
            "document_type": "flight_ticket",
            "summary": "Рейс Таллин — Хельсинки",
            "events": [{
                "title": "Рейс AY101 Таллин — Хельсинки",
                "start_local": "2026-10-01T10:00",
                "end_local": "2026-10-01T11:30",
                "start_timezone": "Europe/Tallinn",
                "end_timezone": "Europe/Helsinki",
                "location": "Таллин → Хельсинки",
                "description": "Рейс AY101",
                "category": "travel",
                "confidence": 0.96,
            }],
            "warnings": [],
        })
        with patch("modules.file_ingest.upload_file_bytes", return_value="file-1") as upload, \
             patch("modules.file_ingest.complete_with_file", return_value=answer), \
             patch("modules.file_ingest.delete_file") as delete, \
             patch("modules.file_ingest.resolve_location_timezone", return_value=None):
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

    def test_pobeda_ticket_uses_same_generic_location_resolution(self):
        answer = json.dumps({
            "document_type": "flight_ticket",
            "summary": "Два рейса Москва — Саратов — Москва",
            "events": [
                {
                    "title": "DP6865 Москва — Саратов",
                    "start_local": "2026-09-18T19:30",
                    "end_local": "2026-09-18T22:05",
                    "start_location": "Москва, аэропорт Шереметьево D",
                    "end_location": "Саратов, аэропорт Гагарин",
                    "start_timezone": "Europe/Moscow",
                    "end_timezone": "Europe/Moscow",
                    "location": "Москва (Шереметьево D) → Саратов (Гагарин)",
                    "description": "Рейс DP6865",
                    "category": "travel",
                    "confidence": 0.99,
                },
                {
                    "title": "DP6866 Саратов — Москва",
                    "start_local": "2026-09-20T23:40",
                    "end_local": "2026-09-21T00:30",
                    "start_location": "Саратов, аэропорт Гагарин",
                    "end_location": "Москва, аэропорт Шереметьево D",
                    "start_timezone": "Europe/Moscow",
                    "end_timezone": "Europe/Moscow",
                    "location": "Саратов (Гагарин) → Москва (Шереметьево D)",
                    "description": "Рейс DP6866",
                    "category": "travel",
                    "confidence": 0.99,
                },
            ],
            "warnings": [],
        })

        def resolve(place: str):
            if "Саратов" in place:
                return {"timezone": "Europe/Saratov", "provider": "test"}
            if "Москва" in place:
                return {"timezone": "Europe/Moscow", "provider": "test"}
            return None

        with patch("modules.file_ingest.upload_file_bytes", return_value="file-pobeda"), \
             patch("modules.file_ingest.complete_with_file", return_value=answer), \
             patch("modules.file_ingest.delete_file"), \
             patch("modules.file_ingest.resolve_location_timezone", side_effect=resolve):
            result = file_ingest.analyze_file_bytes(
                b"ticket",
                filename="document.pdf",
                mimetype="application/pdf",
                user_timezone="Europe/Moscow",
                now=NOW,
            )

        outbound, inbound = result["events"]
        self.assertTrue(outbound["ready"])
        self.assertTrue(outbound["movement"])
        self.assertEqual(outbound["start_timezone"], "Europe/Moscow")
        self.assertEqual(outbound["end_timezone"], "Europe/Saratov")
        self.assertEqual(outbound["start"], "2026-09-18T19:30:00+03:00")
        self.assertEqual(outbound["end"], "2026-09-18T22:05:00+04:00")
        self.assertEqual(outbound["start_location"], "Москва, аэропорт Шереметьево D")
        self.assertEqual(outbound["end_location"], "Саратов, аэропорт Гагарин")

        self.assertTrue(inbound["ready"])
        self.assertTrue(inbound["movement"])
        self.assertEqual(inbound["start_timezone"], "Europe/Saratov")
        self.assertEqual(inbound["end_timezone"], "Europe/Moscow")
        self.assertEqual(inbound["start"], "2026-09-20T23:40:00+04:00")
        self.assertEqual(inbound["end"], "2026-09-21T00:30:00+03:00")

    def test_ambiguous_movement_requires_review_when_location_cannot_be_verified(self):
        with patch("modules.file_ingest.resolve_location_timezone", return_value=None):
            event = file_ingest._normalize_event(
                {
                    "title": "Поезд А — Б",
                    "start_local": "2026-10-01T10:00",
                    "end_local": "2026-10-01T12:00",
                    "start_location": "Город А",
                    "end_location": "Город Б",
                    "start_timezone": "Europe/Moscow",
                    "end_timezone": "Europe/Moscow",
                    "confidence": 0.9,
                },
                document_type="train_ticket",
                user_timezone="Europe/Moscow",
                now=NOW,
            )
        self.assertIsNotNone(event)
        self.assertFalse(event["ready"])
        self.assertTrue(any("независимо проверить" in warning for warning in event["warnings"]))

    def test_travel_event_without_timezone_requires_manual_review(self):
        event = file_ingest._normalize_event(
            {
                "title": "Поездка без часового пояса",
                "start_local": "2026-10-01T10:00",
                "end_local": "2026-10-01T12:00",
                "start_timezone": None,
                "end_timezone": None,
                "confidence": 0.9,
            },
            document_type="travel_ticket",
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

    def test_task_list_item_without_time_is_still_actionable(self):
        task = file_ingest._normalize_task(
            {
                "title": "Подготовить текст рассылки",
                "description": "Согласовать финальную версию",
                "due_local": None,
                "priority": "high",
                "category": "work",
                "confidence": 0.94,
            },
            user_timezone="Europe/Moscow",
            now=NOW,
        )
        self.assertIsNotNone(task)
        self.assertTrue(task["ready"])
        self.assertIsNone(task["due_at"])
        self.assertEqual(task["priority"], "high")
        self.assertEqual(task["category"], "work")

    def test_screenshot_plan_returns_tasks_even_without_calendar_events(self):
        answer = json.dumps({
            "document_type": "task_list",
            "summary": "План дел на день",
            "events": [],
            "tasks": [
                {
                    "title": "Позвонить Косте",
                    "description": "",
                    "due_local": None,
                    "due_timezone": None,
                    "priority": "normal",
                    "category": "work",
                    "estimate_minutes": None,
                    "confidence": 0.96,
                },
                {
                    "title": "Купить продукты",
                    "description": "",
                    "due_local": "2026-09-15T19:00",
                    "due_timezone": "Europe/Moscow",
                    "priority": "normal",
                    "category": "personal",
                    "estimate_minutes": 30,
                    "confidence": 0.91,
                },
            ],
            "warnings": [],
        })
        with patch("modules.file_ingest.upload_file_bytes", return_value="file-tasks"), \
             patch("modules.file_ingest.complete_with_file", return_value=answer), \
             patch("modules.file_ingest.delete_file"), \
             patch("modules.file_ingest.resolve_location_timezone", return_value=None):
            result = file_ingest.analyze_file_bytes(
                b"screenshot",
                filename="document.png",
                mimetype="image/png",
                user_timezone="Europe/Moscow",
                now=NOW,
            )

        self.assertEqual(result["events"], [])
        self.assertEqual(len(result["tasks"]), 2)
        self.assertTrue(result["tasks"][0]["ready"])
        self.assertEqual(result["tasks"][0]["title"], "Позвонить Косте")
        self.assertEqual(result["tasks"][1]["due_at"], "2026-09-15T19:00:00+03:00")

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
