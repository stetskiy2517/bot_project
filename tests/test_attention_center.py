from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core import db
from core.attention_store import (
    dismiss_attention_item,
    get_attention_item,
    list_attention_items,
    mark_attention_push_result,
    pending_attention_pushes,
    upsert_attention_item,
)
from core.navigation_store import (
    queue_navigation_optimization_request,
    remove_navigation_optimization_request,
)
from core.task_planner_store import create_planner_task, update_planner_task
from modules.attention import (
    capture_email_plan_attention,
    sync_navigation_attention,
    sync_overdue_task_attention,
)


class AttentionCenterTests(unittest.TestCase):
    def setUp(self):
        self.user_id = 991000001
        with db.db_lock:
            for table in ("attention_items", "tasks", "navigation_preferences"):
                db.conn.execute(f"DELETE FROM {table} WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    def tearDown(self):
        with db.db_lock:
            for table in ("attention_items", "tasks", "navigation_preferences"):
                db.conn.execute(f"DELETE FROM {table} WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    def test_upsert_deduplicates_and_preserves_action_payload(self):
        first = upsert_attention_item(
            self.user_id,
            source_type="email_action",
            source_key="message-1",
            category="email",
            priority="normal",
            title="Нужно ответить",
            action_type="email_action",
            action={"action_type": "task", "title": "Ответить клиенту"},
        )
        second = upsert_attention_item(
            self.user_id,
            source_type="email_action",
            source_key="message-1",
            category="email",
            priority="high",
            title="Срочно ответить",
            action_type="email_action",
            action={"action_type": "task", "title": "Ответить клиенту"},
        )

        items = list_attention_items(self.user_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(first["attention_id"], second["attention_id"])
        self.assertEqual(items[0]["priority"], "high")
        self.assertEqual(items[0]["action"]["title"], "Ответить клиенту")

    def test_cached_email_action_creates_one_attention_item(self):
        plan = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "actions": [{
                "action_type": "calendar_event",
                "title": "Встреча с клиентом",
                "due_at": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
                "confidence": 0.96,
                "ready": True,
                "source": {
                    "account": "gmail",
                    "provider_message_id": "mail-100",
                    "subject": "Встреча завтра",
                },
            }],
        }
        capture_email_plan_attention(self.user_id, plan)
        capture_email_plan_attention(self.user_id, plan)
        items = list_attention_items(self.user_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_type"], "email_action")
        self.assertEqual(items[0]["priority"], "high")
        self.assertEqual(items[0]["action_type"], "email_action")

    def test_old_cached_email_plan_does_not_reappear_as_fresh_attention(self):
        plan = {
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(),
            "actions": [{
                "action_type": "task",
                "title": "Старое действие",
                "confidence": 0.99,
                "ready": True,
                "source": {"provider_message_id": "old-mail", "subject": "Старое"},
            }],
        }
        self.assertEqual(capture_email_plan_attention(self.user_id, plan), 0)
        self.assertEqual(list_attention_items(self.user_id), [])

    def test_overdue_task_disappears_after_completion(self):
        task = create_planner_task(
            self.user_id,
            "Отправить отчёт",
            due_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            priority="high",
            category="work",
        )
        sync_overdue_task_attention(self.user_id)
        items = list_attention_items(self.user_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["action"]["task_id"], task["task_id"])
        self.assertEqual(items[0]["priority"], "high")

        update_planner_task(self.user_id, task["task_id"], {"status": "done"})
        sync_overdue_task_attention(self.user_id)
        self.assertEqual(list_attention_items(self.user_id), [])

    def test_navigation_conflict_is_high_priority_and_stale_is_dismissed(self):
        queue_navigation_optimization_request(
            self.user_id,
            event_id="event-next",
            previous_event_id="event-prev",
            timezone_name="Europe/Moscow",
            origin="Москва, Тверская 1",
            destination="Москва, Арбат 10",
            required_minutes=45,
            available_minutes=20,
            missing_minutes=25,
            title="Встреча",
            previous_title="Совещание",
        )
        sync_navigation_attention(self.user_id)
        items = list_attention_items(self.user_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_type"], "navigation_optimization")
        self.assertEqual(items[0]["priority"], "high")

        remove_navigation_optimization_request(self.user_id, "event-next")
        sync_navigation_attention(self.user_id)
        self.assertEqual(list_attention_items(self.user_id), [])

    def test_successful_push_is_not_requeued_and_rate_limits_next_batch(self):
        first = upsert_attention_item(
            self.user_id,
            source_type="task_overdue",
            source_key="1:due",
            category="task",
            priority="high",
            title="Первая задача",
        )
        second = upsert_attention_item(
            self.user_id,
            source_type="task_overdue",
            source_key="2:due",
            category="task",
            priority="high",
            title="Вторая задача",
        )
        pending = pending_attention_pushes(self.user_id, limit=1)
        self.assertEqual(len(pending), 1)
        mark_attention_push_result(self.user_id, first["attention_id"], success=True)
        self.assertEqual(pending_attention_pushes(self.user_id, limit=2), [])
        self.assertIsNotNone(get_attention_item(self.user_id, first["attention_id"])["pushed_at"])
        self.assertIsNone(get_attention_item(self.user_id, second["attention_id"])["pushed_at"])

    def test_dismissed_item_stays_hidden_after_same_source_is_synced(self):
        item = upsert_attention_item(
            self.user_id,
            source_type="assistant",
            source_key="same",
            category="assistant",
            priority="normal",
            title="Первый текст",
        )
        self.assertTrue(dismiss_attention_item(self.user_id, item["attention_id"]))
        upsert_attention_item(
            self.user_id,
            source_type="assistant",
            source_key="same",
            category="assistant",
            priority="normal",
            title="Обновлённый текст",
        )
        self.assertEqual(list_attention_items(self.user_id), [])


if __name__ == "__main__":
    unittest.main()
