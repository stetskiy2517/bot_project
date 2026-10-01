from __future__ import annotations

from pathlib import Path
import unittest

from core import db
from core.file_import_store import (
    create_file_import_draft,
    list_file_import_drafts,
    mark_file_import_item,
)
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
    def setUp(self):
        self.user_id = 993100001
        with db.db_lock:
            db.conn.execute("DELETE FROM file_import_drafts WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    def tearDown(self):
        self.setUp()

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

    def test_import_draft_survives_and_tracks_each_action(self):
        result = {
            "summary": "Расписание",
            "tasks": [{
                "title": "Съёмка интервью",
                "due_at": "2026-10-01T12:00:00+03:00",
                "ready": True,
            }],
            "events": [{
                "title": "Планёрка",
                "start": "2026-10-01T10:00:00+03:00",
                "end": "2026-10-01T11:00:00+03:00",
                "ready": True,
            }],
        }
        draft_id = create_file_import_draft(self.user_id, result)
        drafts = list_file_import_drafts(self.user_id)
        self.assertEqual(drafts[0]["draft_id"], draft_id)
        self.assertFalse(drafts[0]["result"]["tasks"][0].get("imported"))

        mark_file_import_item(
            self.user_id,
            draft_id,
            kind="task",
            index=0,
            target="task",
        )
        mark_file_import_item(
            self.user_id,
            draft_id,
            kind="task",
            index=0,
            target="calendar",
        )
        mark_file_import_item(
            self.user_id,
            draft_id,
            kind="event",
            index=0,
            target="calendar",
        )
        restored = list_file_import_drafts(self.user_id)[0]["result"]
        self.assertTrue(restored["tasks"][0]["imported"]["task"])
        self.assertTrue(restored["tasks"][0]["imported"]["calendar"])
        self.assertTrue(restored["events"][0]["imported"]["calendar"])

    def test_file_ingest_ui_can_create_tasks_from_analysis(self):
        source = Path("web/file-ingest.js").read_text(encoding="utf-8")
        self.assertIn("result.tasks", source)
        self.assertIn('"/api/files/task"', source)
        self.assertIn('"/api/files/drafts"', source)
        self.assertIn("restoreImportDrafts", source)
        self.assertIn("draftHasPending", source)
        self.assertIn("Добавить задачу", source)
        self.assertIn("Добавить в календарь", source)
        self.assertIn("applyTaskToCalendar", source)
        self.assertIn("task.due_at", source)
        self.assertIn("ищу задачи, даты и события", source)


if __name__ == "__main__":
    unittest.main()
