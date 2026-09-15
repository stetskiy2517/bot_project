from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

from core.ai_memory_store import list_ai_memory_events, record_ai_memory_event
from core.assistant_preferences import get_assistant_preferences, save_assistant_preferences
from core.db import conn, db_lock, get_or_create_google_user
from core.feature_access import has_ai_access, set_ai_entitlement
from modules.ai_assistant import answer_unhandled
from modules.reminders import REMINDER_CREATE, detect_reminder_intent
from modules.router import INTENT_CREATE, INTENT_UNKNOWN, detect_intent


class AIFeatureAccessTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"AI_ACCESS_MODE": "entitled"})
        self.env.start()
        token = uuid.uuid4().hex
        self.user_id = get_or_create_google_user(
            f"ai-access-{token}",
            f"ai-access-{token}@example.test",
            "AI Access Test",
        )

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
        self.env.stop()

    def test_ai_requires_explicit_entitlement_in_paid_mode(self):
        self.assertFalse(has_ai_access(self.user_id))
        set_ai_entitlement(self.user_id, True, source="test")
        self.assertTrue(has_ai_access(self.user_id))
        set_ai_entitlement(self.user_id, False, source="test")
        self.assertFalse(has_ai_access(self.user_id))

    @patch("modules.ai_assistant.complete", return_value="Этот вызов не должен произойти")
    @patch("modules.ai_assistant.is_ai_available", return_value=True)
    def test_unentitled_user_never_calls_chat_provider(self, _available, complete):
        self.assertIsNone(answer_unhandled("Привет", user_id=self.user_id))
        complete.assert_not_called()

    def test_free_user_content_is_not_copied_into_ai_memory_queue(self):
        secret_text = "Каждый день в 19:00 гуляю с собакой"
        record_ai_memory_event(
            self.user_id,
            "note",
            123,
            "created",
            {"text": secret_text, "title": "Привычка"},
        )

        events = list_ai_memory_events(self.user_id, limit=10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["snapshot"], {"ai_access_skipped": True})
        self.assertNotIn(secret_text, str(events[0]["snapshot"]))

    def test_proactive_ai_preferences_are_effectively_off_without_entitlement(self):
        save_assistant_preferences(
            self.user_id,
            {
                "proactive_reminders_enabled": True,
                "proactive_calendar_events_enabled": True,
            },
        )
        prefs = get_assistant_preferences(self.user_id)
        self.assertFalse(prefs["proactive_reminders_enabled"])
        self.assertFalse(prefs["proactive_calendar_events_enabled"])

    def test_free_calendar_and_reminder_commands_do_not_need_ai(self):
        self.assertEqual(detect_intent("Встреча завтра в 15:00").name, INTENT_CREATE)
        self.assertEqual(
            detect_reminder_intent("Напомни завтра в 09:00 купить молоко"),
            REMINDER_CREATE,
        )
        self.assertEqual(
            detect_intent("Запланируй прогулку с собакой каждый день в 19:00").name,
            INTENT_CREATE,
        )

    def test_free_recurring_calendar_shorthand_stays_deterministic(self):
        self.assertEqual(
            detect_intent("Прогулка с собакой каждый день в 19:00").name,
            INTENT_CREATE,
        )
        self.assertEqual(
            detect_intent("Встреча с командой каждый понедельник в 10:00").name,
            INTENT_CREATE,
        )

    def test_first_person_habit_statement_is_not_mistaken_for_direct_command(self):
        self.assertEqual(
            detect_intent("Я каждый день в 19:00 гуляю с собакой").name,
            INTENT_UNKNOWN,
        )
        self.assertEqual(
            detect_intent("Я каждый вечер в 22:00 принимаю таблетки").name,
            INTENT_UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()
