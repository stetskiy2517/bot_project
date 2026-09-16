from __future__ import annotations

from pathlib import Path
import unittest
from uuid import uuid4

from core.db import conn, db_lock, get_or_create_google_user
from core.note_enhancements import list_enhanced_notes, update_note_metadata
from core.note_store import create_note
from tests.web_test_support import web_test_app


class NoteProductStoreTests(unittest.TestCase):
    USER_ID = 86753131

    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        with db_lock:
            note_ids = [row[0] for row in conn.execute(
                "SELECT note_id FROM notes WHERE user_id=?", (self.USER_ID,)
            ).fetchall()]
            for note_id in note_ids:
                conn.execute(
                    "DELETE FROM note_metadata WHERE user_id=? AND note_id=?",
                    (self.USER_ID, note_id),
                )
                conn.execute(
                    "DELETE FROM ai_memory_events WHERE user_id=? AND entity_type='note' AND entity_id=?",
                    (self.USER_ID, note_id),
                )
            conn.execute("DELETE FROM notes WHERE user_id=?", (self.USER_ID,))
            conn.commit()

    def test_category_is_saved_and_pinned_notes_sort_first(self):
        first = create_note(self.USER_ID, "Обычная заметка", title="Обычная")
        pinned = create_note(self.USER_ID, "Семейные планы", title="Семья")
        saved = update_note_metadata(
            self.USER_ID,
            pinned["note_id"],
            pinned=True,
            category="family",
            tags=["дом", "семья"],
            checklist=[{"text": "Купить билеты", "done": False}],
        )
        self.assertEqual(saved["category"], "family")
        notes = list_enhanced_notes(self.USER_ID, limit=20)
        self.assertEqual(notes[0]["note_id"], pinned["note_id"])
        self.assertTrue(notes[0]["pinned"])
        self.assertEqual(notes[0]["tags"], ["дом", "семья"])
        self.assertIn(first["note_id"], {item["note_id"] for item in notes})

    def test_unknown_category_is_rejected(self):
        note = create_note(self.USER_ID, "Текст", title="Тест")
        with self.assertRaises(ValueError):
            update_note_metadata(
                self.USER_ID,
                note["note_id"],
                pinned=False,
                category="finance-secret-category",
            )


class NoteProductApiTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        stamp = uuid4().hex
        self.user_id = get_or_create_google_user(
            f"notes-product-{stamp}",
            f"notes-product-{stamp}@example.test",
            "Notes Product User",
        )
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id
            stored.permanent = True

    def tearDown(self):
        with db_lock:
            note_ids = [row[0] for row in conn.execute(
                "SELECT note_id FROM notes WHERE user_id=?", (self.user_id,)
            ).fetchall()]
            for note_id in note_ids:
                conn.execute("DELETE FROM note_metadata WHERE user_id=? AND note_id=?", (self.user_id, note_id))
                conn.execute(
                    "DELETE FROM ai_memory_events WHERE user_id=? AND entity_type='note' AND entity_id=?",
                    (self.user_id, note_id),
                )
            conn.execute("DELETE FROM notes WHERE user_id=?", (self.user_id,))
            conn.execute("DELETE FROM google_accounts WHERE user_id=?", (self.user_id,))
            conn.execute("DELETE FROM users WHERE user_id=?", (self.user_id,))
            conn.commit()

    def test_create_list_edit_and_metadata_flow(self):
        created = self.client.post(
            "/api/note-tools",
            json={
                "title": "Поездка",
                "text": "Собрать документы и зарядку",
                "category": "travel",
                "pinned": True,
                "tags": ["отпуск"],
                "checklist": [{"text": "Паспорт", "done": False}],
            },
        )
        self.assertEqual(created.status_code, 201)
        note = created.get_json()["note"]
        note_id = note["note_id"]
        self.assertEqual(note["category"], "travel")
        self.assertTrue(note["pinned"])

        listed = self.client.get("/api/note-tools")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["notes"][0]["note_id"], note_id)

        edited = self.client.patch(
            f"/api/note-tools/{note_id}",
            json={"title": "Документы в поездку", "text": "Паспорт, страховка, зарядка"},
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.get_json()["note"]["title"], "Документы в поездку")

        metadata = self.client.put(
            f"/api/note-tools/{note_id}/metadata",
            json={
                "pinned": False,
                "category": "personal",
                "tags": ["документы"],
                "checklist": [{"text": "Паспорт", "done": True}],
            },
        )
        self.assertEqual(metadata.status_code, 200)
        payload = metadata.get_json()["note"]
        self.assertEqual(payload["category"], "personal")
        self.assertFalse(payload["pinned"])
        self.assertTrue(payload["checklist"][0]["done"])


class NoteProductUiContractTests(unittest.TestCase):
    def test_notes_ui_is_first_class_and_has_no_prompt_editors(self):
        source = Path("web/note-tools.js").read_text(encoding="utf-8")
        self.assertNotIn("prompt(", source)
        self.assertIn("notesLibrarySearch", source)
        self.assertIn("notesSemanticSearch", source)
        self.assertIn("data-note-editor-mode", source)
        self.assertIn("data-note-check", source)
        self.assertIn("PlannerNotes", source)
        self.assertIn("#libraryList .library-card[data-type='note']", source)


if __name__ == "__main__":
    unittest.main()
