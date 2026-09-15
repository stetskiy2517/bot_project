from __future__ import annotations

from datetime import datetime
import unittest
import uuid
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.assistant_preferences import get_assistant_preferences, save_assistant_preferences
from core.db import conn, db_lock, get_or_create_google_user, save_user_timezone
from core.memory_store import upsert_memory
from core.proactive_store import get_proactive_decision, record_proactive_decision
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

    def _habit(
        self,
        *,
        key="evening_medicine",
        confidence=0.97,
        evidence="Я каждый вечер в 22:00 принимаю таблетки",
        source_type="note",
        value="Принять таблетки",
    ):
        return upsert_memory(
            self.user_id,
            "habit",
            key,
            value,
            confidence,
            source_type=source_type,
            source_id="test-source",
            evidence=evidence,
        )

    def test_proactive_actions_are_opt_in(self):
        prefs = get_assistant_preferences(self.user_id)
        self.assertFalse(prefs["proactive_reminders_enabled"])
        self.assertFalse(prefs["proactive_calendar_events_enabled"])
        memory = self._habit()
        result = evaluate_user_proactive(self.user_id, now=self.now)
        self.assertEqual(result["created"], 0)
        self.assertEqual(list_active_reminders(self.user_id), [])
        self.assertIsNone(get_proactive_decision(self.user_id, memory["memory_id"]))

    def test_explicit_high_confidence_legacy_habit_creates_one_recurring_reminder(self):
        save_assistant_preferences(self.user_id, {"proactive_reminders_enabled": True})
        memory = self._habit()

        first = evaluate_user_proactive(self.user_id, now=self.now)
        second = evaluate_user_proactive(self.user_id, now=self.now)

        self.assertEqual(first["created_reminders"], 1)
        self.assertEqual(second["created"], 0)
        reminders = list_active_reminders(self.user_id)
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["text"], "Принять таблетки")
        self.assertEqual(reminders[0]["repeat_rule"], "daily")
        due = datetime.fromisoformat(reminders[0]["remind_at"]).astimezone(ZoneInfo("Europe/Moscow"))
        self.assertEqual((due.hour, due.minute), (22, 0))
        decision = get_proactive_decision(self.user_id, memory["memory_id"], "reminder")
        self.assertEqual(decision["status"], "created")
        self.assertEqual(decision["reminder_id"], reminders[0]["reminder_id"])

    def test_structured_medicine_habit_creates_reminder(self):
        save_assistant_preferences(self.user_id, {"proactive_reminders_enabled": True})
        self._habit(
            evidence="Пользователь явно описал устойчивую рутину.",
            value={
                "statement": "Я каждый вечер в 22:00 принимаю таблетки",
                "action_title": "Принять таблетку",
                "action_type": "reminder",
                "action_confidence": 0.98,
                "schedule": {"repeat": "daily", "time": "22:00"},
            },
        )

        result = evaluate_user_proactive(self.user_id, now=self.now)

        self.assertEqual(result["created_reminders"], 1)
        reminders = list_active_reminders(self.user_id)
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["text"], "Принять таблетку")
        self.assertEqual(reminders[0]["repeat_rule"], "daily")

    @patch("modules.proactive._list_events", return_value=[])
    @patch("modules.proactive._create_event", return_value={"id": "calendar-series-1"})
    def test_daily_dog_walk_creates_one_hour_calendar_event(self, create_event, _list_events):
        save_assistant_preferences(self.user_id, {"proactive_calendar_events_enabled": True})
        memory = self._habit(
            key="dog_walk",
            evidence="Каждый день в 19:00 гуляю с собакой",
            value={
                "statement": "Каждый день в 19:00 гуляет с собакой",
                "action_title": "Погулять с собакой",
                "action_type": "calendar_event",
                "action_confidence": 0.99,
                "schedule": {"repeat": "daily", "time": "19:00"},
                "duration_minutes": 60,
            },
        )

        result = evaluate_user_proactive(self.user_id, now=self.now)

        self.assertEqual(result["created_calendar_events"], 1)
        self.assertEqual(result["created_reminders"], 0)
        self.assertEqual(list_active_reminders(self.user_id), [])
        create_event.assert_called_once()
        event = create_event.call_args.args[1]
        self.assertEqual(event["summary"], "Погулять с собакой")
        self.assertEqual(event["recurrence"], ["RRULE:FREQ=DAILY"])
        start = datetime.fromisoformat(event["start"]["dateTime"])
        end = datetime.fromisoformat(event["end"]["dateTime"])
        self.assertEqual((start.hour, start.minute), (19, 0))
        self.assertEqual(int((end - start).total_seconds() // 60), 60)
        decision = get_proactive_decision(self.user_id, memory["memory_id"], "calendar_event")
        self.assertEqual(decision["status"], "created")
        self.assertEqual(decision["calendar_event_id"], "calendar-series-1")

    @patch("modules.proactive._list_events", return_value=[])
    @patch("modules.proactive._create_event", return_value={"id": "calendar-series-2"})
    def test_scheduled_weekly_call_is_calendar_event(self, create_event, _list_events):
        save_assistant_preferences(self.user_id, {"proactive_calendar_events_enabled": True})
        self._habit(
            key="call_mom",
            evidence="По пятницам в 20:30 звоню маме",
            value={
                "statement": "По пятницам в 20:30 звонит маме",
                "action_title": "Позвонить маме",
                "action_type": "calendar_event",
                "action_confidence": 0.96,
                "schedule": {"repeat": "weekly", "time": "20:30", "weekday": 4},
                "duration_minutes": 60,
            },
        )

        result = evaluate_user_proactive(self.user_id, now=self.now)

        self.assertEqual(result["created_calendar_events"], 1)
        event = create_event.call_args.args[1]
        self.assertEqual(event["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=FR"])
        start = datetime.fromisoformat(event["start"]["dateTime"])
        self.assertEqual(start.weekday(), 4)
        self.assertEqual((start.hour, start.minute), (20, 30))

    @patch("modules.proactive._create_event")
    def test_calendar_event_permission_is_separate_from_reminder_permission(self, create_event):
        save_assistant_preferences(self.user_id, {"proactive_reminders_enabled": True})
        self._habit(
            key="dog_walk",
            evidence="Каждый день в 19:00 гуляю с собакой",
            value={
                "statement": "Каждый день в 19:00 гуляет с собакой",
                "action_title": "Погулять с собакой",
                "action_type": "calendar_event",
                "action_confidence": 0.99,
                "schedule": {"repeat": "daily", "time": "19:00"},
                "duration_minutes": 60,
            },
        )

        result = evaluate_user_proactive(self.user_id, now=self.now)

        self.assertEqual(result["created"], 0)
        create_event.assert_not_called()

    def test_uncertain_structured_habit_does_not_fall_back_to_legacy_reminder(self):
        save_assistant_preferences(
            self.user_id,
            {"proactive_reminders_enabled": True, "proactive_calendar_events_enabled": True},
        )
        memory = self._habit(
            key="uncertain",
            evidence="Каждый вечер в 20:00 что-то делаю",
            value={"statement": "Каждый вечер в 20:00 что-то делает"},
        )

        result = evaluate_user_proactive(self.user_id, now=self.now)

        self.assertEqual(result["created"], 0)
        self.assertEqual(list_active_reminders(self.user_id), [])
        decision = get_proactive_decision(self.user_id, memory["memory_id"], "none")
        self.assertEqual(decision["status"], "not_actionable")

    def test_raw_habit_sentence_becomes_short_action_title(self):
        save_assistant_preferences(self.user_id, {"proactive_reminders_enabled": True})
        self._habit(value="Я каждый вечер в 22:00 принимаю таблетки")

        result = evaluate_user_proactive(self.user_id, now=self.now)

        self.assertEqual(result["created"], 1)
        reminders = list_active_reminders(self.user_id)
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["text"], "Принять таблетку")

    def test_existing_proactive_raw_title_is_repaired_without_recreating_reminder(self):
        save_assistant_preferences(self.user_id, {"proactive_reminders_enabled": True})
        memory = self._habit(value="каждый вечер в 22:00 принимать таблетки")
        due = datetime(2026, 9, 15, 22, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        existing = create_reminder(
            self.user_id,
            "каждый вечер в 22:00 принимать таблетки",
            due,
            repeat_rule="daily",
            repeat_timezone="Europe/Moscow",
        )
        record_proactive_decision(
            self.user_id,
            memory["memory_id"],
            memory.get("updated_at") or "",
            status="created",
            reminder_id=existing["reminder_id"],
            reason="Создано по привычке.",
            confidence=0.97,
            action_type="reminder",
        )

        result = evaluate_user_proactive(self.user_id, now=self.now)

        self.assertEqual(result["created"], 0)
        reminders = list_active_reminders(self.user_id)
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["text"], "Принять таблетку")
        decision = get_proactive_decision(self.user_id, memory["memory_id"], "reminder")
        self.assertEqual(decision["status"], "created")
        self.assertIn("Нормализован", decision["reason"])

    def test_vague_legacy_habit_without_exact_time_is_not_actionable(self):
        save_assistant_preferences(self.user_id, {"proactive_reminders_enabled": True})
        memory = self._habit(evidence="Я каждый вечер принимаю таблетки")
        result = evaluate_user_proactive(self.user_id, now=self.now)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(list_active_reminders(self.user_id), [])
        self.assertEqual(get_proactive_decision(self.user_id, memory["memory_id"], "none")["status"], "not_actionable")

    def test_low_confidence_or_inferred_calendar_habit_never_auto_creates(self):
        save_assistant_preferences(
            self.user_id,
            {"proactive_reminders_enabled": True, "proactive_calendar_events_enabled": True},
        )
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
        decision = get_proactive_decision(self.user_id, memory["memory_id"], "reminder")
        self.assertEqual(decision["status"], "covered")
        self.assertEqual(decision["reminder_id"], existing["reminder_id"])


if __name__ == "__main__":
    unittest.main()
