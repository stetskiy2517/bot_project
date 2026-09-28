from __future__ import annotations

import unittest
from unittest.mock import patch

from modules import memory


class UnifiedMemoryIngestionTests(unittest.TestCase):
    def test_email_source_is_supported(self):
        event = {
            "entity_type": "email",
            "snapshot": {
                "from": "Иван <ivan@example.test>",
                "subject": "КП",
                "body": "Алексей, пришлите предложение до пятницы.",
                "date": "2026-09-28T09:00:00+00:00",
            },
        }
        source = memory._source_payload(event)
        self.assertEqual(source["source"], "email")
        self.assertEqual(source["subject"], "КП")
        self.assertIn("предложение", source["text"])

    def test_reminder_skips_second_work_context_ai_call(self):
        with patch.object(memory, "_call_work_context_model") as model:
            saved = memory._save_work_context(
                {"user_id": 1, "entity_type": "reminder", "entity_id": 1, "event_type": "created", "snapshot": {}},
                {"source": "reminder", "text": "Купить молоко"},
            )
        self.assertEqual(saved, 0)
        model.assert_not_called()

    def test_future_calendar_event_does_not_create_completed_interaction(self):
        raw = {
            "companies": [],
            "contacts": [],
            "interaction": {
                "interaction_type": "meeting",
                "summary": "Встреча с клиентом",
                "outcome": "",
                "next_step": "",
                "company_name": "",
                "contact_name": "",
                "confidence": 0.99,
            },
            "commitments": [],
        }
        event = {
            "user_id": 8811001,
            "entity_type": "calendar_event",
            "entity_id": 12,
            "event_type": "created",
            "snapshot": {"google_event_id": "future-12"},
        }
        with patch.object(memory, "_call_work_context_model", return_value=raw),              patch.object(memory, "record_interaction") as record:
            memory._save_work_context(event, {"source": "calendar_event", "summary": "Встреча"})
        record.assert_not_called()

    def test_high_confidence_company_contact_and_commitment_are_saved(self):
        raw = {
            "companies": [{"name": "Компания А", "industry": "", "website": "", "confidence": 0.95}],
            "contacts": [{
                "full_name": "Иван Петров", "company_name": "Компания А", "position": "Директор",
                "phone": "", "email": "", "telegram": "", "confidence": 0.94,
            }],
            "interaction": None,
            "commitments": [{
                "title": "Отправить КП", "due_at": "", "company_name": "Компания А",
                "contact_name": "Иван Петров", "confidence": 0.96,
            }],
        }
        company = {"company_id": 10, "name": "Компания А"}
        contact = {"contact_id": 20, "full_name": "Иван Петров"}
        event = {"user_id": 8811002, "entity_type": "email", "entity_id": 5, "event_type": "created", "snapshot": {}}
        with patch.object(memory, "_call_work_context_model", return_value=raw),              patch.object(memory, "_find_company", return_value=None),              patch.object(memory, "_find_contact", return_value=None),              patch.object(memory, "create_company", return_value=company) as create_company,              patch.object(memory, "create_contact", return_value=contact) as create_contact,              patch.object(memory, "create_commitment", return_value={"commitment_id": 1}) as create_commitment:
            saved = memory._save_work_context(event, {"source": "email", "text": "test"})
        self.assertEqual(saved, 3)
        create_company.assert_called_once()
        create_contact.assert_called_once()
        create_commitment.assert_called_once()


if __name__ == "__main__":
    unittest.main()
