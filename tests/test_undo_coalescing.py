import time
import unittest
import uuid

from core import db
from core.command_store import enter_request, leave_request
from core.note_store import append_note, create_note, list_notes
from core.undo_store import last_note_action, undo_note_action
from tests.web_test_support import web_test_app


class UndoCoalescingTests(unittest.TestCase):
    def setUp(self):
        value = uuid.uuid4().hex
        self.user = db.get_or_create_google_user(value, value + "@example.test", "Undo Test")

    def test_multiple_note_mutations_in_one_request_undo_as_one_action(self):
        request_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}"
        tokens = enter_request(self.user, request_id)
        try:
            note = create_note(self.user, "Первый текст")
            append_note(self.user, note["note_id"], "Второй текст")
        finally:
            leave_request(tokens)

        self.assertEqual(len(list_notes(self.user)), 1)
        action = last_note_action(self.user)
        self.assertIsNotNone(action)
        undo_note_action(self.user, action["id"])
        self.assertEqual(list_notes(self.user), [])
        self.assertIsNone(last_note_action(self.user))

    def test_real_web_chat_create_then_undo_removes_note(self):
        app = web_test_app()
        client = app.test_client()
        with client.session_transaction() as stored:
            stored["user_id"] = self.user
            stored["auth_time"] = time.time()

        created = client.post("/api/chat", json={"message": "заметка: отменяемое действие"})
        self.assertEqual(created.status_code, 200, created.get_json())
        self.assertEqual(len(list_notes(self.user)), 1)

        status = client.get("/api/assistant")
        self.assertEqual(status.status_code, 200, status.get_json())
        action = status.get_json()["undo"]
        self.assertIsNotNone(action)

        undone = client.post(f"/api/assistant/undo/{action['id']}", json={})
        self.assertEqual(undone.status_code, 200, undone.get_json())
        self.assertEqual(list_notes(self.user), [], undone.get_json())
        self.assertIsNone(client.get("/api/assistant").get_json()["undo"])


if __name__ == "__main__":
    unittest.main()
