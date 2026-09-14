import unittest
import uuid

from core import db
from core.command_store import enter_request, leave_request
from core.note_store import append_note, create_note, list_notes
from core.undo_store import last_note_action, undo_note_action


class UndoCoalescingTests(unittest.TestCase):
    def setUp(self):
        value = uuid.uuid4().hex
        self.user = db.get_or_create_google_user(value, value + "@example.test", "Undo Test")

    def test_multiple_note_mutations_in_one_request_undo_as_one_action(self):
        request_id = f"{int(__import__('time').time() * 1000)}-{uuid.uuid4().hex}"
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


if __name__ == "__main__":
    unittest.main()
