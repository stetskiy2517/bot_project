from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from core.ai_memory_store import list_ai_memory_events, record_ai_memory_event
from core.db import conn, db_lock
from core.memory_store import (
    list_memories,
    memory_prompt_context,
    suppress_memory,
    upsert_memory,
)
from modules.memory import _record_calendar_snapshot, process_memory_event


class MemoryV1Tests(unittest.TestCase):
    def setUp(self):
        stamp = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
        self.user_id = 1_700_000_000 + (stamp % 100_000_000)
        self.other_user_id = self.user_id + 1

    def tearDown(self):
        with db_lock:
            for user_id in (self.user_id, self.other_user_id):
                event_ids = [
                    row[0]
                    for row in conn.execute(
                        "SELECT event_id FROM ai_memory_events WHERE user_id=?", (user_id,)
                    ).fetchall()
                ]
                if event_ids:
                    placeholders = ",".join("?" for _ in event_ids)
                    conn.execute(
                        f"DELETE FROM ai_memory_event_processing WHERE event_id IN ({placeholders})",
                        event_ids,
                    )
                conn.execute("DELETE FROM ai_memory_events WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM ai_calendar_sync WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM user_memories WHERE user_id=?", (user_id,))
            conn.commit()

    def test_memory_is_isolated_by_user(self):
        upsert_memory(
            self.user_id,
            "preference",
            "meeting_time",
            "Предпочитает встречи после 10:00",
            0.95,
            source_type="note",
            source_id=1,
            evidence="Встречи лучше после десяти",
        )
        self.assertEqual(len(list_memories(self.user_id)), 1)
        self.assertEqual(list_memories(self.other_user_id), [])

    def test_weaker_conflicting_memory_does_not_replace_stronger_value(self):
        original = upsert_memory(
            self.user_id,
            "preference",
            "meeting_time",
            "После 10:00",
            0.95,
            source_type="note",
            source_id=1,
            evidence="Я предпочитаю встречи после 10",
        )
        resolved = upsert_memory(
            self.user_id,
            "preference",
            "meeting_time",
            "До 09:00",
            0.60,
            source_type="calendar_event",
            source_id=2,
            evidence="Одна ранняя встреча",
        )
        self.assertEqual(resolved["memory_id"], original["memory_id"])
        self.assertEqual(resolved["value"], "После 10:00")
        self.assertEqual(resolved["confidence"], 0.95)

    def test_suppressed_memory_is_not_sent_to_model_context(self):
        memory = upsert_memory(
            self.user_id,
            "fact",
            "pet_name",
            "Кота зовут Барсик",
            0.99,
            source_type="note",
            source_id=3,
            evidence="Моего кота зовут Барсик",
        )
        self.assertIn("Барсик", memory_prompt_context(self.user_id))
        self.assertTrue(suppress_memory(self.user_id, memory["memory_id"]))
        self.assertNotIn("Барсик", memory_prompt_context(self.user_id))

    @patch("modules.memory._call_memory_model")
    def test_note_event_extracts_memory_without_executing_actions(self, model):
        model.return_value = [
            {
                "kind": "habit",
                "key": "evening_routine",
                "value": "Каждый вечер выполняет личную рутину около 22:00",
                "confidence": 0.96,
                "evidence": "Каждый вечер в 22:00 выполняю рутину",
            }
        ]
        event_id = record_ai_memory_event(
            self.user_id,
            "note",
            17,
            "created",
            {
                "note_id": 17,
                "title": "Вечер",
                "text": "Каждый вечер в 22:00 выполняю рутину",
            },
        )
        with db_lock:
            conn.execute(
                "INSERT OR REPLACE INTO ai_memory_event_processing "
                "(event_id,user_id,attempts,last_error,lease_until,processed_at) "
                "VALUES (?,?,1,NULL,NULL,NULL)",
                (event_id, self.user_id),
            )
            conn.commit()

        saved = process_memory_event(
            {
                "event_id": event_id,
                "user_id": self.user_id,
                "entity_type": "note",
                "entity_id": 17,
                "event_type": "created",
                "snapshot": {
                    "note_id": 17,
                    "title": "Вечер",
                    "text": "Каждый вечер в 22:00 выполняю рутину",
                },
                "attempts": 1,
            }
        )
        self.assertEqual(saved, 1)
        memories = list_memories(self.user_id)
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["kind"], "habit")
        with db_lock:
            processed_at = conn.execute(
                "SELECT processed_at FROM ai_memory_event_processing WHERE event_id=?", (event_id,)
            ).fetchone()[0]
            reminder_count = conn.execute(
                "SELECT COUNT(*) FROM reminders WHERE user_id=?", (self.user_id,)
            ).fetchone()[0]
        self.assertIsNotNone(processed_at)
        self.assertEqual(reminder_count, 0)

    def test_calendar_snapshot_is_journaled_only_when_changed(self):
        snapshot = {
            "google_event_id": "calendar-memory-test",
            "summary": "Футбол",
            "description": "",
            "location": "",
            "start": {"dateTime": "2026-09-20T18:00:00+03:00"},
            "end": {"dateTime": "2026-09-20T19:00:00+03:00"},
            "recurring_event_id": "",
            "color_id": "",
        }
        self.assertTrue(_record_calendar_snapshot(self.user_id, snapshot))
        self.assertFalse(_record_calendar_snapshot(self.user_id, snapshot))
        changed = {**snapshot, "location": "Стадион"}
        self.assertTrue(_record_calendar_snapshot(self.user_id, changed))

        events = [
            item
            for item in list_ai_memory_events(self.user_id, limit=20)
            if item["entity_type"] == "calendar_event"
        ]
        self.assertEqual(len(events), 2)
        self.assertEqual({event["event_type"] for event in events}, {"created", "updated"})


if __name__ == "__main__":
    unittest.main()
