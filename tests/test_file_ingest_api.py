from __future__ import annotations

from pathlib import Path
import unittest

from modules.file_ingest_api import (
    MAX_DOCUMENT_BYTES,
    MAX_IMAGE_BYTES,
    _calendar_proposal,
    _file_type,
)


class _Upload:
    def __init__(self, filename: str, mimetype: str):
        self.filename = filename
        self.mimetype = mimetype


class FileIngestAPITests(unittest.TestCase):
    def test_pdf_uses_document_limit(self):
        name, mimetype, limit = _file_type(_Upload("ticket.pdf", "application/pdf"))
        self.assertEqual(name, "document.pdf")
        self.assertEqual(mimetype, "application/pdf")
        self.assertEqual(limit, MAX_DOCUMENT_BYTES)

    def test_image_uses_image_limit(self):
        name, mimetype, limit = _file_type(_Upload("ticket.png", "image/png"))
        self.assertEqual(name, "document.png")
        self.assertEqual(mimetype, "image/png")
        self.assertEqual(limit, MAX_IMAGE_BYTES)

    def test_browser_xlsx_alias_is_normalized_for_provider(self):
        _, mimetype, _ = _file_type(_Upload(
            "trip.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ))
        self.assertEqual(mimetype, "application/vnd.ms-excel")

    def test_legacy_xls_is_rejected_because_provider_does_not_list_it(self):
        with self.assertRaises(ValueError):
            _file_type(_Upload("trip.xls", "application/vnd.ms-excel"))

    def test_mime_extension_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            _file_type(_Upload("ticket.pdf", "image/png"))


    def test_timed_task_can_be_normalized_for_calendar(self):
        proposal = _calendar_proposal({
            "task": {
                "title": "Съёмка интервью",
                "description": "Ротонда",
                "due_at": "2026-10-01T12:00:00+03:00",
                "due_timezone": "Europe/Moscow",
                "estimate_minutes": 120,
                "category": "work",
                "confidence": 1.0,
                "ready": True,
            }
        })
        self.assertEqual(proposal["start"], "2026-10-01T12:00:00+03:00")
        self.assertEqual(proposal["end"], "2026-10-01T14:00:00+03:00")
        self.assertEqual(proposal["start_timezone"], "Europe/Moscow")

    def test_timed_task_defaults_calendar_duration_to_one_hour(self):
        proposal = _calendar_proposal({
            "task": {
                "title": "Обсуждение целей",
                "due_at": "2026-10-01T11:15:00+03:00",
                "due_timezone": "Europe/Moscow",
                "ready": True,
            }
        })
        self.assertEqual(proposal["end"], "2026-10-01T12:15:00+03:00")

    def test_file_ingest_ui_can_create_tasks_from_analysis(self):
        source = Path("web/file-ingest.js").read_text(encoding="utf-8")
        self.assertIn("result.tasks", source)
        self.assertIn('"/api/tasks"', source)
        self.assertIn("Добавить задачу", source)
        self.assertIn("Добавить в календарь", source)
        self.assertIn("applyTaskToCalendar", source)
        self.assertIn("task.due_at", source)
        self.assertIn("ищу задачи, даты и события", source)


if __name__ == "__main__":
    unittest.main()
