from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

from core.admin_store import list_admin_audit
from core.db import conn, db_lock, get_or_create_google_user
from core.feature_access import has_ai_access
from tests.web_test_support import web_test_app


class AdminPanelTests(unittest.TestCase):
    def setUp(self):
        token = uuid.uuid4().hex
        self.admin_email = f"owner-{token}@example.test"
        self.env = patch.dict(
            os.environ,
            {"ADMIN_EMAILS": self.admin_email.upper(), "AI_ACCESS_MODE": "beta"},
        )
        self.env.start()
        self.app = web_test_app()
        self.admin = self.app.test_client()
        self.user = self.app.test_client()
        self.anonymous = self.app.test_client()
        self.admin_id = get_or_create_google_user(
            f"admin-{token}", self.admin_email, "Owner"
        )
        self.user_id = get_or_create_google_user(
            f"member-{token}", f"member-{token}@example.test", "Member"
        )
        with self.admin.session_transaction() as stored:
            stored["user_id"] = self.admin_id
        with self.user.session_transaction() as stored:
            stored["user_id"] = self.user_id

    def tearDown(self):
        with db_lock:
            conn.execute(
                "DELETE FROM admin_audit_log WHERE admin_user_id IN (?,?) OR target_user_id IN (?,?)",
                (self.admin_id, self.user_id, self.admin_id, self.user_id),
            )
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            for table in tables:
                if table.startswith("sqlite_") or table == "admin_audit_log":
                    continue
                columns = {
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if "user_id" in columns:
                    conn.execute(
                        f"DELETE FROM {table} WHERE user_id IN (?,?)",
                        (self.admin_id, self.user_id),
                    )
            conn.commit()
        self.env.stop()

    def test_admin_page_requires_owner_access(self):
        self.assertEqual(self.anonymous.get("/admin").status_code, 401)
        self.assertEqual(self.user.get("/admin").status_code, 403)
        response = self.admin.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Админка", response.get_data(as_text=True))

    def test_non_admin_cannot_read_admin_api(self):
        response = self.user.get("/api/admin/users")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "forbidden")

    def test_admin_can_list_users_without_sensitive_tokens(self):
        response = self.admin.get("/api/admin/users")
        self.assertEqual(response.status_code, 200)
        users = response.get_json()["users"]
        target = next(item for item in users if item["id"] == self.user_id)
        self.assertEqual(target["email"].split("@")[1], "example.test")
        self.assertNotIn("google_token", target)
        self.assertNotIn("google_sub", target)

    def test_manual_ai_deny_overrides_beta_and_is_audited(self):
        self.assertTrue(has_ai_access(self.user_id))
        response = self.admin.patch(
            f"/api/admin/users/{self.user_id}/features/ai",
            json={"enabled": False, "expires_at": None},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertFalse(has_ai_access(self.user_id))
        updated = response.get_json()["user"]
        self.assertFalse(updated["features"]["ai"]["enabled"])
        self.assertFalse(updated["features"]["ai"]["explicit"]["enabled"])

        events = list_admin_audit(limit=20)
        event = next(item for item in events if item["target_user_id"] == self.user_id)
        self.assertEqual(event["action"], "feature_access_changed")
        self.assertEqual(event["details"]["feature"], "ai")
        self.assertFalse(event["details"]["enabled"])

    def test_admin_can_turn_ai_back_on(self):
        first = self.admin.patch(
            f"/api/admin/users/{self.user_id}/features/ai",
            json={"enabled": False},
        )
        self.assertEqual(first.status_code, 200)
        second = self.admin.patch(
            f"/api/admin/users/{self.user_id}/features/ai",
            json={"enabled": True},
        )
        self.assertEqual(second.status_code, 200)
        self.assertTrue(has_ai_access(self.user_id))

    def test_unknown_feature_is_rejected(self):
        response = self.admin.patch(
            f"/api/admin/users/{self.user_id}/features/not-real",
            json={"enabled": True},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_feature")


if __name__ == "__main__":
    unittest.main()
