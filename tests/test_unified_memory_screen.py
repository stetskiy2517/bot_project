from __future__ import annotations

import unittest
import uuid

from core.db import get_or_create_google_user
from core.memory_store import create_company, create_contact, record_interaction, create_commitment
from tests.web_test_support import web_test_app


class UnifiedMemoryScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = web_test_app()

    def setUp(self):
        token = uuid.uuid4().hex
        self.user_id = get_or_create_google_user(
            f"memory-screen-{token}",
            f"memory-screen-{token}@example.test",
            "Memory Screen",
        )
        other = uuid.uuid4().hex
        self.other_user_id = get_or_create_google_user(
            f"memory-screen-other-{other}",
            f"memory-screen-other-{other}@example.test",
            "Other Memory",
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id

    def test_work_context_is_scoped_to_current_user(self):
        company = create_company(self.user_id, "Моя компания")
        contact = create_contact(self.user_id, "Иван Петров", company_id=company["company_id"])
        record_interaction(
            self.user_id,
            "meeting",
            "Обсудили проект",
            company_id=company["company_id"],
            contact_id=contact["contact_id"],
        )
        create_commitment(
            self.user_id,
            "Отправить предложение",
            company_id=company["company_id"],
            contact_id=contact["contact_id"],
        )
        create_company(self.other_user_id, "Чужая компания")

        response = self.client.get("/api/memory/work-context")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([item["name"] for item in payload["companies"]], ["Моя компания"])
        self.assertEqual([item["full_name"] for item in payload["contacts"]], ["Иван Петров"])
        self.assertEqual(len(payload["interactions"]), 1)
        self.assertEqual(len(payload["commitments"]), 1)

    def test_memory_ui_is_loaded_without_bottom_navigation_item(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<script src="/memory-controls.js"></script>', html)
        self.assertIn('id="memoryScreen"', html)
        self.assertIn('id="closeMemoryScreen"', html)

        script = self.client.get("/memory-controls.js").get_data(as_text=True)
        self.assertIn('id = "openMemoryScreen"', script)
        self.assertIn('settings-theme-link-title">Память<', script)
        self.assertIn('document.getElementById("settingsThemes")', script)
        self.assertIn('themesMenu.appendChild(entry)', script)
        self.assertNotIn('data-view="memory"', script)

        mobile = self.client.get("/mobile-ui.js").get_data(as_text=True)
        self.assertNotIn('["memory"', mobile)

    def test_company_can_be_created_from_memory_api(self):
        response = self.client.post("/api/memory/companies", json={"name": "Новая компания"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["company"]["name"], "Новая компания")


if __name__ == "__main__":
    unittest.main()
