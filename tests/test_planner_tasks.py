from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from core.db import create_task, delete_task, list_tasks, set_task_completed
from modules.router import route_text
from modules.tasks import (
    TASK_COMPLETE,
    TASK_CREATE,
    TASK_DELETE,
    TASK_LIST,
    _task_due_at,
    _task_priority,
    _task_query,
    _task_title,
    detect_task_intent,
    resume_pending_task,
)


class PlannerTaskParsingTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("Europe/Moscow")
        self.now = datetime(2026, 9, 12, 12, 0, tzinfo=self.zone)

    def test_task_intents_are_explicit_and_separate_from_calendar(self):
        cases = {
            "добавь задачу позвонить Иванову": TASK_CREATE,
            "задача купить билеты завтра": TASK_CREATE,
            "какие у меня задачи": TASK_LIST,
            "задачи на завтра": TASK_LIST,
            "отметь задачу позвонить Иванову выполненной": TASK_COMPLETE,
            "задача позвонить Иванову выполнена": TASK_COMPLETE,
            "удали задачу купить билеты": TASK_DELETE,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(detect_task_intent(text), expected)
        self.assertIsNone(detect_task_intent("встреча завтра в 15:00"))
        self.assertIsNone(detect_task_intent("удали встречу завтра"))

    def test_task_title_due_and_priority(self):
        text = "добавь задачу срочно позвонить Иванову завтра в 18:00"
        self.assertEqual(_task_title(text), "Позвонить Иванову")
        self.assertEqual(_task_priority(text), "high")
        self.assertEqual(
            _task_due_at(text, "Europe/Moscow", self.now),
            datetime(2026, 9, 13, 18, 0, tzinfo=self.zone),
        )

    def test_task_can_have_date_without_time(self):
        due = _task_due_at("задача купить подарок до пятницы", "Europe/Moscow", self.now)
        self.assertEqual(due.hour, 23)
        self.assertEqual(due.minute, 59)
        self.assertEqual(due.weekday(), 4)
        self.assertEqual(_task_title("задача купить подарок до пятницы"), "Купить подарок")

    def test_task_query_cleanup(self):
        self.assertEqual(
            _task_query("отметь задачу позвонить Иванову выполненной"),
            "позвонить Иванову",
        )
        self.assertEqual(_task_query("удали задачу купить билеты"), "купить билеты")


class PlannerTaskStorageTests(unittest.TestCase):
    USER_ID = 987654321

    def tearDown(self):
        for status in ("open", "done"):
            for task in list_tasks(self.USER_ID, status=status, limit=500):
                delete_task(self.USER_ID, task["task_id"])

    def test_task_crud_is_user_scoped(self):
        task = create_task(self.USER_ID, "Проверить договор", priority="high")
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["priority"], "high")
        self.assertTrue(any(item["task_id"] == task["task_id"] for item in list_tasks(self.USER_ID)))
        self.assertFalse(any(item["task_id"] == task["task_id"] for item in list_tasks(self.USER_ID + 1)))

        done = set_task_completed(self.USER_ID, task["task_id"], True)
        self.assertEqual(done["status"], "done")
        self.assertFalse(any(item["task_id"] == task["task_id"] for item in list_tasks(self.USER_ID)))
        self.assertTrue(any(item["task_id"] == task["task_id"] for item in list_tasks(self.USER_ID, status="done")))

        self.assertTrue(delete_task(self.USER_ID, task["task_id"]))
        self.assertFalse(delete_task(self.USER_ID, task["task_id"]))


class PlannerTaskRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.update = SimpleNamespace(
            effective_user=SimpleNamespace(id=12345),
            message=SimpleNamespace(text=None, reply_text=AsyncMock()),
        )
        self.context = SimpleNamespace(user_data={})

    async def test_router_sends_explicit_task_to_task_module(self):
        self.update.message.text = "добавь задачу купить билеты"
        with patch("modules.router.handle_task_text", AsyncMock(return_value=True)) as handle_task:
            handled = await route_text(self.update, self.context)
        self.assertTrue(handled)
        handle_task.assert_awaited_once()
        self.assertEqual(handle_task.await_args.args[3], TASK_CREATE)

    async def test_task_pending_delete_confirmation(self):
        pending = {
            "type": "task_confirm_delete",
            "task": {"task_id": 7, "title": "Купить билеты"},
            "timezone": "Europe/Moscow",
        }
        self.context.user_data["smart_planner_pending"] = pending
        with patch("modules.tasks.delete_task", return_value=True) as delete:
            handled = await resume_pending_task(self.update, self.context, "да", pending)
        self.assertTrue(handled)
        delete.assert_called_once_with(12345, 7)
        self.assertNotIn("smart_planner_pending", self.context.user_data)


if __name__ == "__main__":
    unittest.main()
