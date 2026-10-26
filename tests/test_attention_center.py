from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core import db
from core.attention_store import (
    count_active_attention_items,
    dismiss_all_attention_items,
    email_sender_ignore_count,
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
    _email_body,
    capture_email_plan_attention,
    reconcile_email_attention,
    sync_navigation_attention,
    sync_overdue_task_attention,
)


class AttentionCenterTests(unittest.TestCase):
    def setUp(self):
        self.user_id = 991000001
        with db.db_lock:
            for table in ("attention_items", "email_attention_feedback", "tasks", "navigation_preferences"):
                db.conn.execute(f"DELETE FROM {table} WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    def tearDown(self):
        with db.db_lock:
            for table in ("attention_items", "email_attention_feedback", "tasks", "navigation_preferences"):
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

    def test_dismiss_all_is_atomic_and_user_scoped(self):
        first = upsert_attention_item(
            self.user_id, source_type="assistant_notice", source_key="bulk-1",
            category="assistant", priority="high", title="Первое",
        )
        second = upsert_attention_item(
            self.user_id, source_type="assistant_notice", source_key="bulk-2",
            category="assistant", priority="normal", title="Второе",
        )
        other_user = self.user_id + 55
        foreign = upsert_attention_item(
            other_user, source_type="assistant_notice", source_key="foreign",
            category="assistant", priority="high", title="Чужое",
        )
        try:
            self.assertEqual(dismiss_all_attention_items(self.user_id), 2)
            self.assertEqual(dismiss_all_attention_items(self.user_id), 0)
            self.assertEqual(list_attention_items(self.user_id), [])
            self.assertEqual(pending_attention_pushes(self.user_id), [])
            self.assertIsNotNone(get_attention_item(self.user_id, first["attention_id"])["dismissed_at"])
            self.assertIsNotNone(get_attention_item(self.user_id, second["attention_id"])["dismissed_at"])
            self.assertIsNone(get_attention_item(other_user, foreign["attention_id"])["dismissed_at"])
            upsert_attention_item(
                self.user_id, source_type="assistant_notice", source_key="bulk-1",
                category="assistant", priority="high", title="Первое обновлено",
            )
            self.assertEqual(list_attention_items(self.user_id), [])
        finally:
            with db.db_lock:
                db.conn.execute("DELETE FROM attention_items WHERE user_id=?", (other_user,))
                db.conn.commit()

    def test_nonurgent_insurance_offer_does_not_need_attention(self):
        plan = {"created_at": datetime.now(timezone.utc).isoformat(), "actions": [{
            "action_type": "task", "title": "Рассмотреть предложение о страховании жилья",
            "confidence": 0.99, "ready": True,
            "source": {"subject": "Защитите жилье в Т-Страховании",
                       "from": "marketing@bank.example"},
        }]}
        self.assertEqual(capture_email_plan_attention(self.user_id, plan), 0)
        self.assertEqual(list_attention_items(self.user_id), [])
        self.assertEqual(pending_attention_pushes(self.user_id), [])

    def test_old_insurance_and_expired_boarding_are_retired(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(hours=18)).isoformat()
        for idx, action in enumerate([
            {"action_type": "task", "title": "Рассмотреть предложение о страховании жилья",
             "confidence": 0.99, "ready": True,
             "source": {"subject": "Защитите жилье в Т-Страховании", "from": "bank@example.test"}},
            {"action_type": "task", "title": "Посадка в самолёт",
             "due_at": yesterday, "confidence": 0.99, "ready": True,
             "source": {"subject": "Посадка в самолёт", "from": "airline@example.test"}},
            {"action_type": "task", "title": "Регистрация в аэропорту",
             "due_at": yesterday, "confidence": 0.99, "ready": True,
             "source": {"subject": "Регистрация в аэропорту", "from": "airline@example.test"}},
        ]):
            upsert_attention_item(
                self.user_id, source_type="email_action", source_key=f"old-{idx}",
                category="email", priority="high", title=f"Нужно решение по письму: {action['title']}",
                action_type="email_action", action=action,
            )
        cancelled = {"action_type": "task", "title": "Проверить детали аннулированного перелёта",
                     "confidence": 0.98, "ready": True,
                     "source": {"subject": "Аннулирован перелёт по брони XYZ",
                                "from": "airline@example.test"}}
        upsert_attention_item(
            self.user_id, source_type="email_action", source_key="cancel",
            category="email", priority="high",
            title="Нужно решение по письму: Проверить детали аннулированного перелёта",
            action_type="email_action", action=cancelled,
        )
        self.assertEqual(reconcile_email_attention(self.user_id), 3)
        items = list_attention_items(self.user_id)
        self.assertEqual(len(items), 1)
        self.assertIn("аннулированного", items[0]["title"])
        self.assertEqual(items[0]["priority"], "normal")
        self.assertEqual(email_sender_ignore_count(self.user_id, "airline@example.test"), 0)

    def test_sender_learning_only_from_individual_dismissals(self):
        now = datetime.now(timezone.utc)
        for number in range(3):
            action = {"action_type": "task", "title": "Проверить документ",
                      "due_at": (now + timedelta(hours=12)).isoformat(),
                      "confidence": 0.99, "ready": True,
                      "source": {"subject": "Документ", "from": "same@example.test",
                                 "provider_message_id": f"mail-{number}"}}
            capture_email_plan_attention(self.user_id, {"actions": [action]})
            item = list_attention_items(self.user_id)[0]
            # Use the actual stored id, not a client-provided user id.
            self.assertTrue(dismiss_attention_item(self.user_id, item["attention_id"]))
        self.assertEqual(email_sender_ignore_count(self.user_id, "Same <same@example.test>"), 3)
        quiet = {"action_type": "task", "title": "Проверить очередной документ",
                 "due_at": (now + timedelta(hours=12)).isoformat(),
                 "confidence": 0.99, "ready": True,
                 "source": {"subject": "Документ", "from": "same@example.test",
                            "provider_message_id": "quiet-mail"}}
        capture_email_plan_attention(self.user_id, {"actions": [quiet]})
        self.assertEqual(list_attention_items(self.user_id), [])

        urgent = dict(quiet)
        urgent["due_at"] = (now + timedelta(hours=2)).isoformat()
        urgent["source"] = {**quiet["source"], "provider_message_id": "urgent-mail"}
        capture_email_plan_attention(self.user_id, {"actions": [urgent]})
        self.assertEqual(len(list_attention_items(self.user_id)), 1)
        self.assertEqual(list_attention_items(self.user_id)[0]["priority"], "high")

    def test_bulk_hide_and_completion_do_not_train_sender(self):
        for index in range(7):
            upsert_attention_item(
                self.user_id, source_type="email_action", source_key=f"bulk-{index}",
                category="email", priority="high", title=f"Письмо {index}",
                action_type="email_action",
                action={"action_type": "task", "title": "Подписать договор",
                        "confidence": 0.99, "ready": True,
                        "source": {"from": "partner@example.test", "subject": "Договор"}},
            )
        self.assertEqual(count_active_attention_items(self.user_id), 7)
        self.assertEqual(len(list_attention_items(self.user_id, limit=5)), 5)
        self.assertEqual(dismiss_all_attention_items(self.user_id), 7)
        self.assertEqual(email_sender_ignore_count(self.user_id, "partner@example.test"), 0)
        item = upsert_attention_item(
            self.user_id, source_type="email_action", source_key="completed",
            category="email", priority="high", title="Выполнить",
            action_type="email_action",
            action={"action_type": "task", "title": "Ответить",
                    "source": {"from": "partner@example.test", "subject": "Договор"}},
        )
        dismiss_attention_item(self.user_id, item["attention_id"], feedback=False)
        self.assertEqual(email_sender_ignore_count(self.user_id, "partner@example.test"), 0)
        self.assertEqual(list_attention_items(self.user_id), [])

    def test_far_future_deadline_is_held_quietly_until_approaching(self):
        now = datetime.now(timezone.utc)
        action = {"action_type": "task", "title": "Подписать договор",
                  "due_at": (now + timedelta(hours=60)).isoformat(),
                  "confidence": 0.99, "ready": True,
                  "source": {"subject": "Прошу подписать договор",
                             "from": "partner@example.test"}}
        capture_email_plan_attention(self.user_id, {"actions": [action]})
        self.assertEqual(list_attention_items(self.user_id), [])
        self.assertEqual(count_active_attention_items(self.user_id), 0)
        self.assertEqual(reconcile_email_attention(self.user_id, now=now + timedelta(hours=30)), 0)
        items = list_attention_items(self.user_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["priority"], "high")

    def test_email_time_shows_user_timezone_not_utc(self):
        db.save_user_timezone(self.user_id, "Europe/Moscow")
        action = {"due_at": "2026-10-25T17:20:00+00:00",
                  "source": {"subject": "Регистрация в аэропорту"}}
        body = _email_body(action, self.user_id)
        self.assertIn("25.10, 20:20", body)
        self.assertNotIn("UTC", body)

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
