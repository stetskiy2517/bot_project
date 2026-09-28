from __future__ import annotations

import unittest

from core import db
from core.memory_store import (
    create_commitment,
    create_company,
    create_contact,
    list_commitments,
    list_companies,
    list_contacts,
    record_interaction,
    set_commitment_status,
)


class SalesMemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.user_id = 992000001
        self.other_user_id = 992000002
        with db.db_lock:
            for table in ("sales_interactions", "sales_commitments", "sales_contacts", "sales_companies"):
                db.conn.execute(f"DELETE FROM {table} WHERE user_id IN (?,?)", (self.user_id, self.other_user_id))
            db.conn.commit()

    def tearDown(self):
        self.setUp()

    def test_company_and_contact_are_user_scoped(self):
        company = create_company(self.user_id, "Росатом", industry="Промышленность")
        contact = create_contact(self.user_id, "Иван Иванов", company_id=company["company_id"], position="Руководитель")
        self.assertEqual(len(list_companies(self.user_id)), 1)
        self.assertEqual(len(list_contacts(self.user_id)), 1)
        self.assertEqual(list_companies(self.other_user_id), [])
        self.assertEqual(list_contacts(self.other_user_id), [])
        self.assertEqual(contact["company_id"], company["company_id"])

    def test_duplicate_company_name_is_rejected_for_same_user(self):
        create_company(self.user_id, "Компания А")
        with self.assertRaises(ValueError):
            create_company(self.user_id, "компания а")
        create_company(self.other_user_id, "Компания А")

    def test_interaction_can_link_company_contact_and_source(self):
        company = create_company(self.user_id, "Компания Б")
        contact = create_contact(self.user_id, "Петр Петров", company_id=company["company_id"])
        interaction = record_interaction(
            self.user_id,
            "meeting",
            "Обсудили поставку материалов",
            company_id=company["company_id"],
            contact_id=contact["contact_id"],
            outcome="Запросили КП",
            next_step="Отправить КП",
            source_type="calendar_event",
            source_id="event-123",
        )
        self.assertEqual(interaction["interaction_type"], "meeting")
        self.assertEqual(interaction["source_id"], "event-123")

    def test_commitment_keeps_completion_history(self):
        company = create_company(self.user_id, "Компания В")
        item = create_commitment(self.user_id, "Отправить КП", company_id=company["company_id"])
        self.assertEqual(len(list_commitments(self.user_id)), 1)
        done = set_commitment_status(self.user_id, item["commitment_id"], "done")
        self.assertEqual(done["status"], "done")
        self.assertIsNotNone(done["completed_at"])
        self.assertEqual(list_commitments(self.user_id), [])
        self.assertEqual(len(list_commitments(self.user_id, status="done")), 1)

    def test_foreign_company_cannot_be_linked(self):
        foreign = create_company(self.other_user_id, "Чужая компания")
        with self.assertRaises(ValueError):
            create_contact(self.user_id, "Не должен сохраниться", company_id=foreign["company_id"])


if __name__ == "__main__":
    unittest.main()
