from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from core.db import get_or_create_google_user
from core.memory_store import (
    create_company, create_contact, create_commitment, record_interaction,
    search_work_memory, work_memory_prompt_context,
)
from modules import ai_assistant
from tests.web_test_support import web_test_app


class CompleteUnifiedMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = web_test_app()

    def setUp(self):
        token = uuid.uuid4().hex
        self.user_id = get_or_create_google_user(
            f"complete-memory-{token}", f"complete-memory-{token}@example.test", "Memory User"
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id

    def test_search_returns_related_company_contact_history_and_commitment(self):
        company = create_company(self.user_id, "Росатом")
        contact = create_contact(self.user_id, "Иван Петров", company_id=company["company_id"], position="Директор")
        record_interaction(self.user_id, "meeting", "Обсудили поставку материалов", company_id=company["company_id"], contact_id=contact["contact_id"])
        create_commitment(self.user_id, "Отправить КП", company_id=company["company_id"], contact_id=contact["contact_id"])
        result = search_work_memory(self.user_id, "Росатом")
        self.assertEqual(len(result["companies"]), 1)
        self.assertEqual(len(result["contacts"]), 1)
        self.assertEqual(len(result["interactions"]), 1)
        self.assertEqual(len(result["commitments"]), 1)

    def test_assistant_prompt_contains_work_memory(self):
        company = create_company(self.user_id, "Росатом")
        create_contact(self.user_id, "Иван Петров", company_id=company["company_id"], position="Директор")
        prompt = ai_assistant._system_prompt_for_user(self.user_id, "кто такой Иван Петров")
        self.assertIn("Рабочая память", prompt)
        self.assertIn("Иван Петров", prompt)
        self.assertIn("Росатом", prompt)

    def test_search_api_is_user_scoped(self):
        create_company(self.user_id, "Моя компания")
        other = get_or_create_google_user("complete-memory-other-"+uuid.uuid4().hex, "other-"+uuid.uuid4().hex+"@example.test", "Other")
        create_company(other, "Чужая компания")
        response = self.client.get("/api/memory/search?q=компания")
        self.assertEqual(response.status_code, 200)
        names = [item["name"] for item in response.get_json()["companies"]]
        self.assertIn("Моя компания", names)
        self.assertNotIn("Чужая компания", names)

    def test_company_and_contact_can_be_corrected_and_commitment_completed(self):
        company = create_company(self.user_id, "Старое имя")
        contact = create_contact(self.user_id, "Иван", company_id=company["company_id"])
        commitment = create_commitment(self.user_id, "Отправить КП", company_id=company["company_id"], contact_id=contact["contact_id"])
        response = self.client.patch(f"/api/memory/companies/{company['company_id']}", json={"name":"Новое имя","industry":"Строительство"})
        self.assertEqual(response.status_code, 200)
        response = self.client.patch(f"/api/memory/contacts/{contact['contact_id']}", json={"full_name":"Иван Петров","position":"Директор"})
        self.assertEqual(response.status_code, 200)
        response = self.client.patch(f"/api/memory/commitments/{commitment['commitment_id']}", json={"status":"done"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["commitment"]["status"], "done")

    def test_sourced_commitment_is_idempotent(self):
        first = create_commitment(self.user_id, "Отправить КП", source_type="email", source_id="msg-1")
        second = create_commitment(self.user_id, "Отправить КП", source_type="email", source_id="msg-1")
        self.assertEqual(first["commitment_id"], second["commitment_id"])


if __name__ == "__main__":
    unittest.main()
