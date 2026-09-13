from datetime import datetime, timedelta, timezone
import unittest

from core.ai_memory_store import list_ai_memory_events
from core.db import conn, db_lock
from core.library_store import get_saved_reminder, list_saved_reminders
from core.note_store import create_note, delete_note, get_note, list_notes, search_notes
from core.reminder_store import claim_due_reminders, create_reminder, delete_saved_reminder, list_reminders


class AiMemoryRetentionTests(unittest.TestCase):
    def setUp(self):
        stamp = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
        self.user_id = 1_100_000_000 + (stamp % 100_000_000)

    def test_deleted_note_disappears_for_user_but_row_and_memory_event_remain(self):
        note = create_note(self.user_id, "Молоко, хлеб, яблоки", title="Список покупок")
        self.assertTrue(delete_note(self.user_id, note["note_id"]))

        self.assertIsNone(get_note(self.user_id, note["note_id"]))
        self.assertFalse(any(item["note_id"] == note["note_id"] for item in list_notes(self.user_id)))
        self.assertEqual(search_notes(self.user_id, "Список покупок"), [])

        with db_lock:
            row = conn.execute(
                "SELECT title,text,deleted_at FROM notes WHERE user_id=? AND note_id=?",
                (self.user_id, note["note_id"]),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Список покупок")
        self.assertEqual(row[1], "Молоко, хлеб, яблоки")
        self.assertIsNotNone(row[2])

        events = list_ai_memory_events(self.user_id, limit=20)
        deleted = next(
            event
            for event in events
            if event["entity_type"] == "note"
            and event["entity_id"] == note["note_id"]
            and event["event_type"] == "deleted"
        )
        self.assertEqual(deleted["snapshot"]["title"], "Список покупок")
        self.assertEqual(deleted["snapshot"]["text"], "Молоко, хлеб, яблоки")
        self.assertIsNotNone(deleted["snapshot"]["deleted_at"])

    def test_deleted_reminder_cannot_fire_but_is_retained_for_ai_memory(self):
        reminder = create_reminder(
            self.user_id,
            "Выпить таблетку",
            datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        self.assertTrue(delete_saved_reminder(self.user_id, reminder["reminder_id"]))

        self.assertIsNone(get_saved_reminder(self.user_id, reminder["reminder_id"]))
        self.assertFalse(
            any(item["reminder_id"] == reminder["reminder_id"] for item in list_saved_reminders(self.user_id))
        )
        self.assertFalse(
            any(item["reminder_id"] == reminder["reminder_id"] for item in list_reminders(self.user_id))
        )
        self.assertEqual(claim_due_reminders(self.user_id), [])

        with db_lock:
            row = conn.execute(
                "SELECT text,remind_at,status,deleted_at FROM reminders "
                "WHERE user_id=? AND reminder_id=?",
                (self.user_id, reminder["reminder_id"]),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Выпить таблетку")
        self.assertIsNotNone(row[3])

        events = list_ai_memory_events(self.user_id, limit=20)
        deleted = next(
            event
            for event in events
            if event["entity_type"] == "reminder"
            and event["entity_id"] == reminder["reminder_id"]
            and event["event_type"] == "deleted"
        )
        self.assertEqual(deleted["snapshot"]["text"], "Выпить таблетку")
        self.assertEqual(deleted["snapshot"]["status"], "pending")
        self.assertIsNotNone(deleted["snapshot"]["deleted_at"])

    def test_memory_journal_keeps_meaningful_lifecycle_events(self):
        note = create_note(self.user_id, "Черновик", title="Проект")
        reminder = create_reminder(
            self.user_id,
            "Проверить отчёт",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )

        events = list_ai_memory_events(self.user_id, limit=20)
        self.assertTrue(
            any(
                event["entity_type"] == "note"
                and event["entity_id"] == note["note_id"]
                and event["event_type"] == "created"
                for event in events
            )
        )
        self.assertTrue(
            any(
                event["entity_type"] == "reminder"
                and event["entity_id"] == reminder["reminder_id"]
                and event["event_type"] == "created"
                for event in events
            )
        )


if __name__ == "__main__":
    unittest.main()
