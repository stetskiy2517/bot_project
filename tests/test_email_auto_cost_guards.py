from __future__ import annotations

import unittest

from core import db
from core.email_auto_store import (
    account_is_initialized,
    initialize_account,
    message_fingerprint,
    message_was_processed,
    set_email_auto_enabled,
)
from modules import email_auto


class AutomaticEmailCostGuardTests(unittest.TestCase):
    def setUp(self):
        self.user_id = 980000011
        self.account = {
            "account_id": 71011,
            "provider": "gmail",
            "email": "cost@example.test",
            "display_name": "Gmail",
            "enabled": True,
        }
        with db.db_lock:
            for table in ("email_auto_messages", "email_auto_accounts", "email_auto_runs", "email_auto_preferences"):
                db.conn.execute(f"DELETE FROM {table} WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    def tearDown(self):
        with db.db_lock:
            for table in ("email_auto_messages", "email_auto_accounts", "email_auto_runs", "email_auto_preferences"):
                db.conn.execute(f"DELETE FROM {table} WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    @staticmethod
    def message(message_id: str, *, subject: str, body: str, attachments=None) -> dict:
        return {
            "provider_message_id": message_id,
            "from": "sender@example.test",
            "subject": subject,
            "date": "2026-09-16T08:00:00+03:00",
            "body": body,
            "preview": body,
            "attachments": list(attachments or []),
        }

    def test_small_newsletter_logo_is_not_an_ai_candidate(self):
        message = self.message(
            "m1",
            subject="Еженедельные новости",
            body="Новая подборка материалов компании",
            attachments=[{"filename": "logo.png", "mime_type": "image/png", "size": 4096}],
        )
        candidate, attachment, text_signal = email_auto._is_candidate(message)
        self.assertFalse(candidate)
        self.assertFalse(attachment)
        self.assertFalse(text_signal)

    def test_ticket_image_is_candidate_even_without_explicit_date_in_body(self):
        message = self.message(
            "m2",
            subject="Ваш заказ",
            body="Спасибо за покупку",
            attachments=[{"filename": "boarding_ticket.png", "mime_type": "image/png", "size": 120000}],
        )
        candidate, attachment, _ = email_auto._is_candidate(message)
        self.assertTrue(candidate)
        self.assertTrue(attachment)

    def test_reenable_forces_fresh_no_ai_baseline(self):
        set_email_auto_enabled(self.user_id, True)
        old = self.message("old", subject="Билет", body="Рейс завтра в 15:00")
        initialize_account(self.user_id, self.account, [old])
        fingerprint = message_fingerprint(self.account, old)
        self.assertTrue(account_is_initialized(self.user_id, self.account["account_id"]))
        self.assertTrue(message_was_processed(self.user_id, self.account["account_id"], fingerprint))

        set_email_auto_enabled(self.user_id, False)
        set_email_auto_enabled(self.user_id, True)

        self.assertFalse(account_is_initialized(self.user_id, self.account["account_id"]))
        self.assertFalse(message_was_processed(self.user_id, self.account["account_id"], fingerprint))


if __name__ == "__main__":
    unittest.main()
