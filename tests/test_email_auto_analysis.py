from __future__ import annotations

import unittest
from unittest.mock import patch

from core import db
from core.email_auto_store import (
    email_auto_enabled,
    message_fingerprint,
    set_email_auto_enabled,
)
from modules import email_auto


class AutomaticEmailAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.user_id = 980000001
        self.account = {
            "account_id": 71001,
            "user_id": self.user_id,
            "provider": "gmail",
            "email": "auto@example.test",
            "display_name": "Gmail",
            "enabled": True,
        }
        with db.db_lock:
            for table in ("email_auto_messages", "email_auto_accounts", "email_auto_runs", "email_auto_preferences"):
                db.conn.execute(f"DELETE FROM {table} WHERE user_id=?", (self.user_id,))
            db.conn.commit()
        set_email_auto_enabled(self.user_id, True)

    def tearDown(self):
        with db.db_lock:
            for table in ("email_auto_messages", "email_auto_accounts", "email_auto_runs", "email_auto_preferences"):
                db.conn.execute(f"DELETE FROM {table} WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    @staticmethod
    def message(message_id: str, *, subject: str = "Новости", body: str = "Обычное письмо", attachments=None):
        return {
            "provider_message_id": message_id,
            "from": "sender@example.test",
            "subject": subject,
            "date": "2026-09-16T08:00:00+03:00",
            "body": body,
            "preview": body,
            "attachments": list(attachments or []),
        }

    def patches(self, messages):
        return (
            patch.object(email_auto, "email_auto_enabled", return_value=True),
            patch.object(email_auto, "has_ai_access", return_value=True),
            patch.object(email_auto, "is_ai_available", return_value=True),
            patch.object(email_auto, "list_email_accounts", return_value=[self.account]),
            patch.object(email_auto, "_read_account", return_value=list(messages)),
        )

    def test_first_cycle_creates_baseline_without_any_ai_call(self):
        messages = [self.message("old-1", subject="Билет", body="Рейс завтра в 15:00")]
        contexts = self.patches(messages)
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], \
             patch.object(email_auto, "_analyze_candidates") as analyze:
            result = email_auto.evaluate_user_email_auto(self.user_id)

        analyze.assert_not_called()
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(result["ai_text_calls"], 0)

    def test_same_message_is_never_reanalyzed(self):
        baseline = self.message("old-1")
        contexts = self.patches([baseline])
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], \
             patch.object(email_auto, "_analyze_candidates") as analyze:
            email_auto.evaluate_user_email_auto(self.user_id)
        analyze.assert_not_called()

        new = self.message("new-1", subject="Встреча", body="Подтверди встречу завтра в 14:00")
        plan = {"actions": [], "attachment_analysis": {"analyzed": 0}, "auto_calendar": {}}
        with patch.object(email_auto, "email_auto_enabled", return_value=True), \
             patch.object(email_auto, "has_ai_access", return_value=True), \
             patch.object(email_auto, "is_ai_available", return_value=True), \
             patch.object(email_auto, "list_email_accounts", return_value=[self.account]), \
             patch.object(email_auto, "_read_account", return_value=[new, baseline]), \
             patch.object(email_auto, "_analyze_candidates", return_value=(plan, 1)) as analyze:
            first = email_auto.evaluate_user_email_auto(self.user_id)
            second = email_auto.evaluate_user_email_auto(self.user_id)

        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(first["ai_text_calls"], 1)
        self.assertEqual(second["ai_text_calls"], 0)
        self.assertEqual(second["candidates"], 0)

    def test_irrelevant_new_message_is_ignored_without_ai(self):
        baseline = self.message("old-1")
        contexts = self.patches([baseline])
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
            email_auto.evaluate_user_email_auto(self.user_id)

        noise = self.message("noise-1", subject="Новинки магазина", body="Скидка на коллекцию товаров")
        with patch.object(email_auto, "email_auto_enabled", return_value=True), \
             patch.object(email_auto, "has_ai_access", return_value=True), \
             patch.object(email_auto, "is_ai_available", return_value=True), \
             patch.object(email_auto, "list_email_accounts", return_value=[self.account]), \
             patch.object(email_auto, "_read_account", return_value=[noise, baseline]), \
             patch.object(email_auto, "_analyze_candidates") as analyze:
            result = email_auto.evaluate_user_email_auto(self.user_id)

        analyze.assert_not_called()
        self.assertEqual(result["new_messages"], 1)
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(result["ai_text_calls"], 0)

    def test_multiple_actionable_text_messages_are_batched_into_one_ai_call(self):
        baseline = self.message("old-1")
        contexts = self.patches([baseline])
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
            email_auto.evaluate_user_email_auto(self.user_id)

        messages = [
            self.message("new-1", subject="Встреча", body="Подтверди встречу завтра в 14:00"),
            self.message("new-2", subject="Счёт", body="Необходимо оплатить счёт до 20 сентября"),
            baseline,
        ]
        plan = {"actions": [], "attachment_analysis": {"analyzed": 0}, "auto_calendar": {}}
        with patch.object(email_auto, "email_auto_enabled", return_value=True), \
             patch.object(email_auto, "has_ai_access", return_value=True), \
             patch.object(email_auto, "is_ai_available", return_value=True), \
             patch.object(email_auto, "list_email_accounts", return_value=[self.account]), \
             patch.object(email_auto, "_read_account", return_value=messages), \
             patch.object(email_auto, "_analyze_candidates", return_value=(plan, 1)) as analyze:
            result = email_auto.evaluate_user_email_auto(self.user_id)

        analyze.assert_called_once()
        self.assertEqual(len(analyze.call_args.args[1]), 2)
        self.assertTrue(analyze.call_args.kwargs["analyze_text"])
        self.assertEqual(result["ai_text_calls"], 1)

    def test_attachment_only_candidate_skips_text_ai_batch(self):
        baseline = self.message("old-1")
        contexts = self.patches([baseline])
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
            email_auto.evaluate_user_email_auto(self.user_id)

        ticket = self.message(
            "ticket-1",
            subject="Ваш документ",
            body="Спасибо за заказ",
            attachments=[{"filename": "ticket.pdf", "mime_type": "application/pdf", "size": 1000, "attachment_id": "a1"}],
        )
        plan = {"actions": [], "attachment_analysis": {"analyzed": 1}, "auto_calendar": {}}
        with patch.object(email_auto, "email_auto_enabled", return_value=True), \
             patch.object(email_auto, "has_ai_access", return_value=True), \
             patch.object(email_auto, "is_ai_available", return_value=True), \
             patch.object(email_auto, "list_email_accounts", return_value=[self.account]), \
             patch.object(email_auto, "_read_account", return_value=[ticket, baseline]), \
             patch.object(email_auto, "_analyze_candidates", return_value=(plan, 0)) as analyze:
            result = email_auto.evaluate_user_email_auto(self.user_id)

        analyze.assert_called_once()
        self.assertFalse(analyze.call_args.kwargs["analyze_text"])
        self.assertEqual(result["ai_text_calls"], 0)
        self.assertEqual(result["attachments_analyzed"], 1)

    def test_imap_fingerprint_does_not_depend_on_sequence_number(self):
        account = {**self.account, "provider": "yandex"}
        first = self.message("101", subject="Билет", body="Рейс Москва Санкт-Петербург")
        second = dict(first)
        second["provider_message_id"] = "207"
        self.assertEqual(message_fingerprint(account, first), message_fingerprint(account, second))

    def test_auto_analysis_is_opt_in(self):
        other = self.user_id + 1
        with db.db_lock:
            db.conn.execute("DELETE FROM email_auto_preferences WHERE user_id=?", (other,))
            db.conn.commit()
        self.assertFalse(email_auto_enabled(other))


if __name__ == "__main__":
    unittest.main()
