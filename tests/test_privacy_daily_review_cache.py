from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import unittest
import uuid

from core import db
from modules import daily_review
from modules.account_privacy import ERASE_CONFIRMATION, create_erase_challenge, erase_account, export_account


class DailyReviewPrivacyContractTests(unittest.TestCase):
    def setUp(self):
        daily_review._init_ai_review_cache()
        token = uuid.uuid4().hex
        self.user_id = db.get_or_create_google_user(
            f"privacy-review-{token}",
            f"privacy-review-{token}@example.test",
            "Privacy Review Test",
        )
        db.save_user_timezone(self.user_id, "Europe/Moscow")
        self.source_hash = hashlib.sha256(token.encode()).hexdigest()
        with db.db_lock:
            db.conn.execute(
                "INSERT OR REPLACE INTO daily_review_ai_cache "
                "(user_id,day,kind,source_hash,text,created_at) VALUES (?,?,?,?,?,?)",
                (
                    self.user_id,
                    "2026-09-17",
                    "morning",
                    self.source_hash,
                    "Личная сводка",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            db.conn.commit()

    def tearDown(self):
        with db.db_lock:
            db.conn.execute("DELETE FROM daily_review_ai_cache WHERE user_id=?", (self.user_id,))
            db.conn.execute("DELETE FROM google_accounts WHERE user_id=?", (self.user_id,))
            db.conn.execute("DELETE FROM users WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    def test_daily_review_cache_is_exported_and_erased_with_account(self):
        exported = export_account(self.user_id)
        rows = exported["daily_review_ai_cache"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_hash"], self.source_hash)
        self.assertEqual(rows[0]["text"], "Личная сводка")

        erase_account(
            self.user_id,
            create_erase_challenge(self.user_id),
            ERASE_CONFIRMATION,
        )

        with db.db_lock:
            remaining = db.conn.execute(
                "SELECT COUNT(*) FROM daily_review_ai_cache WHERE user_id=?",
                (self.user_id,),
            ).fetchone()[0]
        self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
