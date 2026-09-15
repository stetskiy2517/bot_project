from __future__ import annotations

from datetime import datetime
import unittest
import uuid
from zoneinfo import ZoneInfo

from core.assistant_preferences import get_assistant_preferences, save_assistant_preferences
from core.db import conn, db_lock, get_or_create_google_user, save_user_timezone
from core.memory_store import upsert_memory
from core.proactive_store import get_proactive_decision
from core.reminder_store import create_reminder, list_active_reminders
from modules.proactive import evaluate_user_proactive


class ProactiveReminderTests(unittest.TestCase):
    def setUp(self):
        token = uuid.uuid4().hex
        self.user_id = get_or_create_google_user(
            f"proactive-{token}",
            f"proactive-{token}@example.test",
            "Proactive Test",
        )
        save_user_timezone(self.user_id, "Europe/Moscow")
        self.now = datetime(2026, 9, 15, 15, 0, tzinfo=ZoneInfo("Europe/Moscow"))

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

    def _habit(self, *, confidence=0.97, evidence="Я каждый вечер в 22:00 принимаю таблетки", source_type="note"):
        return upsert_memory(
            self.user_id,
            "habit",
            "evening_medicine",
            "Принять таблетки",
            confidence,
            source_type=source_type,
            source_id="test-source",
            evidence=evidence,
        )

    def test_proactive_reminders_are_opt_in(self):
        self.assertFalse(get_assistant_preferences(self.user_id)["proactive_reminders_enabled"])
        memory = self._habit()
        result = evaluate_user_proactive(self.user_id, now=self.now)
        self.assertEqual(result["created"], 0)
        self.assertEqual(list_active_reminders(self.user_id), [])
        self.assertIsNone(get_proactive_decision(self.user_id, memory["memory_id"]))

    def test_explicit_high_confidence_habit_creates_one_recurring_reminder(self):
        save_assistant_preferences(self.user_id, {"proactive_reminders_enabled": True})
        memory = self._habit()

        first = evaluate_user_proactive(self.user_id, now=self.now)
        second = evaluate_user_proactive(self.user_id, now=self.now)

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        reminders = list_active_reminders(self.user_id)
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["text"], "Принять таблетки")
        self.assertEqual(reminders[0]["repeat_rule"], "daily")
        self.assertEqual(reminders[0]["repeat_timezone"], "Europe/Moscow")
        due = datetime.fromisoformat(reminders[0]["remind_at"]).astimezone(ZoneInfo("Europe/Moscow"))
        self.assertEqual((due.hour, due.minute), (22, 0))
        decision = get_proactive_decision(self.user_id, memory["memory_id"])
        self.assertEqual(decision["status"], "created")
        self.assertEqual(decision["reminder_id"], reminders[0]["reminder_id"])

    def test_vague_habit_without_exact_time_is_not_actionable(self):
        save_assistant_preferences(self.user_id, {"proactive_reminders_enabled": True})
        memory = self._habit(evidence="Я каждый вечер принимаю таблетки")
        result = evaluate_user_proactive(self.user_id, now=self.now)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(list_active_reminders(self.user_id), [])
        self.assertEqual(get_proactive_decision(self.user_id, memory["memory_id"])["status"], "not_actionable")

    def test_low_confidence_or_inferred_calendar_habit_never_auto_creates(self):
        save_assistant_preferences(self.user_id, {"proactive_reminders_enabled": True})
        self._habit(confidence=0.80)
        self._habit(
            confidence=0.98,
            source_type="calendar_event",
            evidence="Каждый вечер в 22:00 принимает таблетки",
        )
        result = evaluate_user_proactive(self.user_id, now=self.now)
        self.assertEqual(result["created"], 0)
        self.assertEqual(list_active_reminders(self.user_id), [])

    def test_existing_matching_recurring_reminder_is_respected(self):
        save_assistant_preferences(self.user_id, {"proactive_reminders_enabled": True})
        due = datetime(2026, 9, 15, 22, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        existing = create_reminder(
            self.user_id,
            "Принять таблетки",
            due,
            repeat_rule="daily",
            repeat_timezone="Europe/Moscow",
        )
        memory = self._habit()

        result = evaluate_user_proactive(self.user_id, now=self.now)

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["covered"], 1)
        self.assertEqual(len(list_active_reminders(self.user_id)), 1)
        decision = get_proactive_decision(self.user_id, memory["memory_id"])
        self.assertEqual(decision["status"], "covered")
        self.assertEqual(decision["reminder_id"], existing["reminder_id"])


if __name__ == "__main__":
    unittest.main()
