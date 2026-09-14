import unittest
from uuid import uuid4

import web_app
from core.ai_memory_store import list_ai_memory_events
from core.db import get_or_create_google_user
from core.note_store import create_note, get_note


class LibraryNoteDeleteTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()
        web_app._user_state.clear()
        stamp = uuid4().hex
        self.user_id = get_or_create_google_user(
            f"note-delete-{stamp}",
            f"note-delete-{stamp}@example.test",
            "Note Delete User",
        )
        self.other_user_id = get_or_create_google_user(
            f"note-delete-other-{stamp}",
            f"note-delete-other-{stamp}@example.test",
            "Other Note Delete User",
        )
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id
            session.permanent = True

    def test_note_delete_api_soft_deletes_current_users_note(self):
        note = create_note(self.user_id, "Купить воду", title="Список покупок")
        response = self.client.delete(f"/api/library/notes/{note['note_id']}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        response.close()

        self.assertIsNone(get_note(self.user_id, note["note_id"]))
        library = self.client.get("/api/library").get_json()
        self.assertFalse(any(item["id"] == note["note_id"] for item in library["notes"]))

        events = list_ai_memory_events(self.user_id, limit=20)
        self.assertTrue(
            any(
                event["entity_type"] == "note"
                and event["entity_id"] == note["note_id"]
                and event["event_type"] == "deleted"
                for event in events
            )
        )

    def test_note_delete_api_cannot_delete_another_users_note(self):
        note = create_note(self.other_user_id, "Чужая заметка", title="Чужое")
        response = self.client.delete(f"/api/library/notes/{note['note_id']}")
        self.assertEqual(response.status_code, 404)
        response.close()
        self.assertIsNotNone(get_note(self.other_user_id, note["note_id"]))

    def test_library_script_exposes_note_delete_only_after_swipe(self):
        response = self.client.get("/library.js")
        script = response.get_data(as_text=True)
        response.close()
        self.assertIn('row.className = "library-swipe-row note-swipe-row"', script)
        self.assertIn('deleteButton.dataset.action = "delete-note"', script)
        self.assertIn('/api/library/notes/${Number(noteId)}', script)
        self.assertIn('row.dataset.leftWidth = "0"', script)
        self.assertIn('else if (total > 44) setSwipeOffset(start.row, 88);', script)


if __name__ == "__main__":
    unittest.main()
