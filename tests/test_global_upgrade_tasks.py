from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.task_planner_store import (
    create_planner_task,
    delete_planner_task,
    list_planner_tasks,
    update_planner_task,
)
from modules.task_planner import preview_flexible_schedule


class ExtendedTaskStoreTests(unittest.TestCase):
    USER_ID = 86753091

    def tearDown(self):
        for task in list_planner_tasks(self.USER_ID, status=None, limit=500):
            delete_planner_task(self.USER_ID, task["task_id"])

    def test_extended_task_fields_are_persisted_without_breaking_legacy_table(self):
        due = datetime.now(ZoneInfo("Europe/Moscow")) + timedelta(days=2)
        task = create_planner_task(
            self.USER_ID,
            "Подготовить смету",
            due_at=due.isoformat(),
            priority="high",
            category="work",
            estimate_minutes=90,
            flexible=True,
        )
        self.assertEqual(task["category"], "work")
        self.assertEqual(task["estimate_minutes"], 90)
        self.assertTrue(task["flexible"])
        self.assertEqual(task["priority"], "high")

        updated = update_planner_task(
            self.USER_ID,
            task["task_id"],
            {"estimate_minutes": 120, "category": "personal", "flexible": False},
        )
        self.assertEqual(updated["estimate_minutes"], 120)
        self.assertEqual(updated["category"], "personal")
        self.assertFalse(updated["flexible"])

    def test_subtask_must_belong_to_same_user(self):
        parent = create_planner_task(self.USER_ID, "Большая задача")
        child = create_planner_task(self.USER_ID, "Шаг 1", parent_task_id=parent["task_id"])
        self.assertEqual(child["parent_task_id"], parent["task_id"])
        with self.assertRaises(ValueError):
            create_planner_task(self.USER_ID + 1, "Чужой шаг", parent_task_id=parent["task_id"])


class FlexibleTaskPreviewTests(unittest.TestCase):
    USER_ID = 86753092

    def tearDown(self):
        for task in list_planner_tasks(self.USER_ID, status=None, limit=500):
            delete_planner_task(self.USER_ID, task["task_id"])

    def test_preview_is_read_only_and_prefers_high_priority(self):
        zone = ZoneInfo("Europe/Moscow")
        now = datetime(2026, 9, 16, 9, 0, tzinfo=zone)
        low = create_planner_task(
            self.USER_ID,
            "Низкий приоритет",
            due_at=(now + timedelta(days=1)).isoformat(),
            priority="low",
            estimate_minutes=30,
        )
        high = create_planner_task(
            self.USER_ID,
            "Высокий приоритет",
            due_at=(now + timedelta(days=1)).isoformat(),
            priority="high",
            estimate_minutes=30,
        )
        with patch("modules.task_planner.get_user_timezone", return_value="Europe/Moscow"), \
             patch("modules.task_planner.get_calendar_preferences", return_value={
                 "work_start": "09:00", "work_end": "18:00", "work_days": [0, 1, 2, 3, 4],
                 "buffer_minutes": 0, "category_colors": {},
             }), \
             patch("modules.task_planner._list_events", return_value=[]):
            preview = preview_flexible_schedule(self.USER_ID, now=now)

        self.assertEqual([item["task_id"] for item in preview["proposals"]], [high["task_id"], low["task_id"]])
        stored = {item["task_id"]: item for item in list_planner_tasks(self.USER_ID, status="open")}
        self.assertIsNone(stored[high["task_id"]]["calendar_event_id"])
        self.assertIsNone(stored[low["task_id"]]["calendar_event_id"])

    def test_preview_skips_tasks_without_estimate_or_deadline(self):
        create_planner_task(self.USER_ID, "Без длительности", due_at=(datetime.now(ZoneInfo("Europe/Moscow")) + timedelta(days=1)).isoformat())
        create_planner_task(self.USER_ID, "Без срока", estimate_minutes=30)
        with patch("modules.task_planner.get_user_timezone", return_value="Europe/Moscow"), \
             patch("modules.task_planner.get_calendar_preferences", return_value={
                 "work_start": "09:00", "work_end": "18:00", "work_days": [0, 1, 2, 3, 4],
                 "buffer_minutes": 0, "category_colors": {},
             }):
            preview = preview_flexible_schedule(self.USER_ID)
        self.assertEqual(preview["proposals"], [])
        reasons = {item["reason"] for item in preview["skipped"]}
        self.assertIn("missing_estimate", reasons)
        self.assertIn("missing_deadline", reasons)


if __name__ == "__main__":
    unittest.main()
