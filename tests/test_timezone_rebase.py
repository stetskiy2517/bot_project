from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from core.db import conn, db_lock, init_db, save_user_timezone
from core.reminder_store import init_reminder_store
from core.task_planner_store import init_task_planner_store


class TimezoneRebaseTests(unittest.TestCase):
    USER_ID = 987654321

    def setUp(self):
        init_db()
        init_reminder_store()
        init_task_planner_store()
        with db_lock:
            conn.execute("DELETE FROM reminders WHERE user_id=?", (self.USER_ID,))
            conn.execute("DELETE FROM tasks WHERE user_id=?", (self.USER_ID,))
            conn.execute("DELETE FROM users WHERE user_id=?", (self.USER_ID,))
            conn.execute(
                "INSERT INTO users (user_id,timezone) VALUES (?,?)",
                (self.USER_ID, "Europe/Moscow"),
            )
            conn.execute(
                "INSERT INTO reminders "
                "(user_id,text,remind_at,status,created_at,repeat_rule,repeat_timezone,next_remind_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    self.USER_ID,
                    "Выпить таблетки",
                    "2026-09-20T20:00:00+00:00",
                    "pending",
                    "2026-09-20T12:00:00+00:00",
                    "daily",
                    "Europe/Moscow",
                    "2026-09-21T20:00:00+00:00",
                ),
            )
            conn.execute(
                "INSERT INTO reminders "
                "(user_id,text,remind_at,status,created_at) VALUES (?,?,?,?,?)",
                (
                    self.USER_ID,
                    "Разовое напоминание",
                    "2026-09-22T20:00:00+00:00",
                    "pending",
                    "2026-09-20T12:00:00+00:00",
                ),
            )
            conn.execute(
                "INSERT INTO tasks "
                "(user_id,title,description,due_at,status,priority,created_at,category,flexible,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    self.USER_ID,
                    "Задача на вечер",
                    "",
                    "2026-09-23T20:00:00+00:00",
                    "open",
                    "normal",
                    "2026-09-20T12:00:00+00:00",
                    "personal",
                    1,
                    "2026-09-20T12:00:00+00:00",
                ),
            )
            conn.execute(
                "INSERT INTO reminders "
                "(user_id,text,remind_at,status,created_at,delivered_at) VALUES (?,?,?,?,?,?)",
                (
                    self.USER_ID,
                    "Старое напоминание",
                    "2026-09-19T20:00:00+00:00",
                    "delivered",
                    "2026-09-19T12:00:00+00:00",
                    "2026-09-19T20:00:00+00:00",
                ),
            )
            conn.execute(
                "INSERT INTO tasks "
                "(user_id,title,description,due_at,status,priority,created_at,completed_at,category,flexible,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self.USER_ID,
                    "Выполненная задача",
                    "",
                    "2026-09-19T20:00:00+00:00",
                    "done",
                    "normal",
                    "2026-09-19T12:00:00+00:00",
                    "2026-09-19T20:00:00+00:00",
                    "personal",
                    1,
                    "2026-09-19T20:00:00+00:00",
                ),
            )
            conn.commit()

    def tearDown(self):
        with db_lock:
            conn.execute("DELETE FROM reminders WHERE user_id=?", (self.USER_ID,))
            conn.execute("DELETE FROM tasks WHERE user_id=?", (self.USER_ID,))
            conn.execute("DELETE FROM users WHERE user_id=?", (self.USER_ID,))
            conn.commit()

    def _local_hour(self, value, timezone_name):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")

    def test_moscow_to_saratov_keeps_wall_clock_time(self):
        save_user_timezone(self.USER_ID, "Europe/Saratov")

        with db_lock:
            reminders = conn.execute(
                "SELECT remind_at,next_remind_at,repeat_timezone "
                "FROM reminders WHERE user_id=? ORDER BY reminder_id",
                (self.USER_ID,),
            ).fetchall()
            task = conn.execute(
                "SELECT due_at FROM tasks WHERE user_id=?",
                (self.USER_ID,),
            ).fetchone()

        self.assertEqual(
            self._local_hour(reminders[0][0], "Europe/Saratov"),
            "2026-09-20 23:00",
        )
        self.assertEqual(
            self._local_hour(reminders[0][1], "Europe/Saratov"),
            "2026-09-21 23:00",
        )
        self.assertEqual(reminders[0][2], "Europe/Saratov")
        self.assertEqual(
            self._local_hour(reminders[1][0], "Europe/Saratov"),
            "2026-09-22 23:00",
        )
        self.assertEqual(
            self._local_hour(task[0], "Europe/Saratov"),
            "2026-09-23 23:00",
        )

    def test_timezone_change_does_not_rewrite_completed_history(self):
        with db_lock:
            reminder_before = conn.execute(
                "SELECT remind_at FROM reminders WHERE user_id=? AND text='Старое напоминание'",
                (self.USER_ID,),
            ).fetchone()[0]
            task_before = conn.execute(
                "SELECT due_at FROM tasks WHERE user_id=? AND title='Выполненная задача'",
                (self.USER_ID,),
            ).fetchone()[0]

        save_user_timezone(self.USER_ID, "Europe/Saratov")

        with db_lock:
            reminder_after = conn.execute(
                "SELECT remind_at FROM reminders WHERE user_id=? AND text='Старое напоминание'",
                (self.USER_ID,),
            ).fetchone()[0]
            task_after = conn.execute(
                "SELECT due_at FROM tasks WHERE user_id=? AND title='Выполненная задача'",
                (self.USER_ID,),
            ).fetchone()[0]

        self.assertEqual(reminder_after, reminder_before)
        self.assertEqual(task_after, task_before)

    def test_same_user_timezone_repairs_stale_recurring_schedule(self):
        # Simulate a legacy row after the account timezone was already changed,
        # while the recurring reminder still carries its old Moscow schedule.
        with db_lock:
            conn.execute(
                "UPDATE users SET timezone='Europe/Saratov' WHERE user_id=?",
                (self.USER_ID,),
            )
            conn.commit()

        save_user_timezone(self.USER_ID, "Europe/Saratov")

        with db_lock:
            reminder = conn.execute(
                "SELECT remind_at,next_remind_at,repeat_timezone "
                "FROM reminders WHERE user_id=? AND repeat_rule='daily'",
                (self.USER_ID,),
            ).fetchone()
            task = conn.execute(
                "SELECT due_at FROM tasks WHERE user_id=? AND status='open'",
                (self.USER_ID,),
            ).fetchone()

        self.assertEqual(
            self._local_hour(reminder[0], "Europe/Saratov"),
            "2026-09-20 23:00",
        )
        self.assertEqual(
            self._local_hour(reminder[1], "Europe/Saratov"),
            "2026-09-21 23:00",
        )
        self.assertEqual(reminder[2], "Europe/Saratov")
        self.assertEqual(
            self._local_hour(task[0], "Europe/Saratov"),
            "2026-09-23 23:00",
        )

    def test_round_trip_timezone_change_does_not_drift(self):
        save_user_timezone(self.USER_ID, "Europe/Saratov")
        save_user_timezone(self.USER_ID, "Europe/Moscow")

        with db_lock:
            reminder = conn.execute(
                "SELECT remind_at,next_remind_at,repeat_timezone "
                "FROM reminders WHERE user_id=? AND repeat_rule='daily'",
                (self.USER_ID,),
            ).fetchone()
            task = conn.execute(
                "SELECT due_at FROM tasks WHERE user_id=?",
                (self.USER_ID,),
            ).fetchone()

        self.assertEqual(
            self._local_hour(reminder[0], "Europe/Moscow"),
            "2026-09-20 23:00",
        )
        self.assertEqual(
            self._local_hour(reminder[1], "Europe/Moscow"),
            "2026-09-21 23:00",
        )
        self.assertEqual(reminder[2], "Europe/Moscow")
        self.assertEqual(
            self._local_hour(task[0], "Europe/Moscow"),
            "2026-09-23 23:00",
        )


if __name__ == "__main__":
    unittest.main()
