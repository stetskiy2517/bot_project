from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from core.ai_memory_store import list_ai_memory_events, record_ai_memory_event
from core.db import conn, db_lock


class ReminderMemoryTimezoneTests(unittest.TestCase):
    def setUp(self):
        self.user_id = 1_800_000_000 + (uuid.uuid4().int % 100_000_000)

    def tearDown(self):
        with db_lock:
            conn.execute("DELETE FROM ai_memory_events WHERE user_id=?", (self.user_id,))
            conn.execute("DELETE FROM feature_entitlements WHERE user_id=?", (self.user_id,))
            conn.commit()

    @patch("core.ai_memory_store.get_user_timezone", return_value="Europe/Moscow")
    def test_reminder_clock_is_journaled_in_local_timezone(self, _timezone):
        record_ai_memory_event(
            self.user_id,
            "reminder",
            77,
            "created",
            {
                "reminder_id": 77,
                "text": "Выпить таблетки",
                "remind_at": "2026-09-16T20:00:00+00:00",
                "repeat_rule": "daily",
                "repeat_timezone": "Europe/Moscow",
                "next_remind_at": "2026-09-17T20:00:00+00:00",
            },
        )

        snapshot = list_ai_memory_events(self.user_id, limit=1)[0]["snapshot"]
        self.assertEqual(snapshot["remind_at"], "2026-09-16T23:00:00+03:00")
        self.assertEqual(snapshot["remind_at_utc"], "2026-09-16T20:00:00+00:00")
        self.assertEqual(snapshot["next_remind_at"], "2026-09-17T23:00:00+03:00")
        self.assertEqual(snapshot["memory_timezone"], "Europe/Moscow")


if __name__ == "__main__":
    unittest.main()
