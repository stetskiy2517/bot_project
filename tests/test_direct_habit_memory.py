from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

import web_app
from core.ai_memory_store import list_ai_memory_events
from core.db import conn, db_lock, get_or_create_google_user
from core.note_store import list_notes
from modules.reminders import detect_reminder_intent
from modules.router import INTENT_CREATE, INTENT_UNKNOWN, detect_intent
from tests.web_test_support import web_test_app


class DirectHabitMemoryTests(unittest.TestCase):
    def setUp(self):
        token = uuid.uuid4().hex
        self.user_id = get_or_create_google_user(
            f"direct-habit-{token}",
            f"direct-habit-{token}@example.test",
            "Direct Habit",
        )
        self.app = web_test_app()
        self.client = self.app.test_client()
        web_app._user_state.clear()
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def tearDown(self):
        with db_lock:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for table in tables:
                if table.startswith("sqlite_"):
                    continue
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if "user_id" in columns:
                    conn.execute(f"DELETE FROM {table} WHERE user_id=?", (self.user_id,))
            conn.commit()

    def test_recurring_life_statements_are_not_direct_calendar_commands(self):
        statements = (
            "Я каждый день в 19:00 гуляю с собакой",
            "Я каждый вечер в 22:00 принимаю таблетки",
            "По пятницам в 20:30 звоню маме",
        )
        for text in statements:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_UNKNOWN)
                self.assertIsNone(detect_reminder_intent(text))

    def test_explicit_calendar_commands_still_bypass_memory_interpretation(self):
        self.assertEqual(
            detect_intent("Запланируй прогулку с собакой каждый день в 19:00").name,
            INTENT_CREATE,
        )
        self.assertEqual(detect_intent("Встреча завтра в 15:00").name, INTENT_CREATE)

    def test_plain_habit_message_goes_to_memory_without_creating_note(self):
        text = "Я каждый день в 19:00 гуляю с собакой"
        with patch("modules.assistant_api.answer_unhandled", return_value="Понял твою привычку."):
            response = self.client.post("/api/chat", json={"message": text})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["handled"])
        self.assertEqual(payload["replies"], ["Понял твою привычку."])
        self.assertEqual(list_notes(self.user_id), [])

        events = list_ai_memory_events(self.user_id, limit=10)
        matching = [
            event for event in events
            if event["entity_type"] == "voice_transcript"
            and event["snapshot"].get("text") == text
            and event["snapshot"].get("channel") == "chat"
        ]
        self.assertEqual(len(matching), 1)


if __name__ == "__main__":
    unittest.main()
