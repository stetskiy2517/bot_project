from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

import web_app
from core.db import get_or_create_google_user
from core.note_store import create_note
from core.reminder_store import claim_due_reminders, create_reminder
from modules.note_conversation import ACTIVE_NOTE_KEY


class WebLibraryTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()
        web_app._user_state.clear()
        stamp = uuid4().hex
        self.user_id = get_or_create_google_user(
            f"library-user-{stamp}",
            f"library-{stamp}@example.test",
            "Library User",
        )
        self.other_user_id = get_or_create_google_user(
            f"library-other-{stamp}",
            f"library-other-{stamp}@example.test",
            "Other User",
        )
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id
            session.permanent = True

    def test_library_requires_authentication(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/library").status_code, 401)
        self.assertEqual(
            anonymous.post("/api/library/open", json={"type": "note", "id": 1}).status_code,
            401,
        )

    def test_library_returns_own_notes_and_all_saved_reminder_states(self):
        note = create_note(self.user_id, "Бананы, масло, вода", title="Список покупок")
        create_note(self.other_user_id, "Секрет другой учётки", title="Чужая заметка")

        now = datetime.now(timezone.utc)
        active = create_reminder(self.user_id, "Купить молоко", now + timedelta(hours=2))
        delivered = create_reminder(self.user_id, "Позвонить врачу", now - timedelta(minutes=5))
        claim_due_reminders(self.user_id, now=now)
        create_reminder(self.other_user_id, "Чужое напоминание", now + timedelta(hours=1))

        response = self.client.get("/api/library")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        response.close()

        note_ids = {item["id"] for item in payload["notes"]}
        self.assertIn(note["note_id"], note_ids)
        self.assertTrue(all("Чужая заметка" != item["title"] for item in payload["notes"]))

        reminders = {item["id"]: item for item in payload["reminders"]}
        self.assertEqual(reminders[active["reminder_id"]]["status"], "pending")
        self.assertEqual(reminders[delivered["reminder_id"]]["status"], "delivered")
        self.assertTrue(all(item["text"] != "Чужое напоминание" for item in payload["reminders"]))

    def test_open_note_sets_active_chat_note_context(self):
        note = create_note(self.user_id, "Бананы, масло", title="Список покупок")
        response = self.client.post(
            "/api/library/open",
            json={"type": "note", "id": note["note_id"]},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        response.close()
        self.assertEqual(payload["label"], "Список покупок")
        self.assertIn("Бананы, масло", payload["chat_text"])
        self.assertEqual(
            web_app._user_state[self.user_id][ACTIVE_NOTE_KEY]["note_id"],
            note["note_id"],
        )

    def test_open_reminder_clears_active_note_and_sets_reminder_context(self):
        note = create_note(self.user_id, "Черновик", title="Проект")
        self.client.post("/api/library/open", json={"type": "note", "id": note["note_id"]})
        self.assertIn(ACTIVE_NOTE_KEY, web_app._user_state[self.user_id])

        reminder = create_reminder(
            self.user_id,
            "Забрать документы",
            datetime.now(timezone.utc) + timedelta(hours=3),
        )
        response = self.client.post(
            "/api/library/open",
            json={"type": "reminder", "id": reminder["reminder_id"]},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        response.close()
        self.assertEqual(payload["label"], "Напоминание")
        self.assertIn("Забрать документы", payload["chat_text"])
        self.assertNotIn(ACTIVE_NOTE_KEY, web_app._user_state[self.user_id])
        self.assertEqual(
            web_app._user_state[self.user_id]["smart_planner_active_reminder"]["reminder_id"],
            reminder["reminder_id"],
        )

    def test_open_rejects_another_users_items(self):
        other_note = create_note(self.other_user_id, "Не показывать", title="Чужое")
        other_reminder = create_reminder(
            self.other_user_id,
            "Не показывать напоминание",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        self.assertEqual(
            self.client.post(
                "/api/library/open",
                json={"type": "note", "id": other_note["note_id"]},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/api/library/open",
                json={"type": "reminder", "id": other_reminder["reminder_id"]},
            ).status_code,
            404,
        )

    def test_invalid_library_item_is_rejected(self):
        self.assertEqual(
            self.client.post("/api/library/open", json={"type": "calendar", "id": 1}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/library/open", json={"type": "note", "id": "bad"}).status_code,
            400,
        )

    def test_shell_loads_swipe_library_script(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        response.close()
        self.assertIn('src="/library.js"', html)

        response = self.client.get("/library.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()
        for marker in [
            "libraryScreen",
            "libraryNotesTab",
            "libraryRemindersTab",
            'touchstart',
            'touchend',
            '"wheel"',
            '"/api/library"',
            '"/api/library/open"',
            "showDocumentInChat",
            "closeChatToMain",
            "chatCollapseBtn",
            'dx > 0 && app.classList.contains("chat-active")',
            'wheelX < 0 && app.classList.contains("chat-active")',
        ]:
            self.assertIn(marker, script)


if __name__ == "__main__":
    unittest.main()
