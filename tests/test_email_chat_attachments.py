from __future__ import annotations

import unittest
from unittest.mock import patch

from modules.email_api import answer_email_chat_request


class EmailChatAttachmentPlanningTests(unittest.TestCase):
    @patch("modules.email_api.build_email_plan")
    def test_deep_chat_request_uses_attachment_pipeline_and_prioritizes_ticket_events(self, build_plan):
        build_plan.return_value = {
            "summary": "Старая переписка просит подтвердить заказ до 12:00.",
            "actions": [
                {
                    "action_type": "calendar_event",
                    "title": "DP6865 Москва — Саратов",
                    "due_at": "2099-09-18T19:30:00+03:00",
                    "confidence": 0.99,
                    "source": {"subject": "Маршрут-квитанция", "attachment": "ticket-out.pdf"},
                    "ready": True,
                    "warnings": [],
                    "attachment_event": {
                        "title": "DP6865 Москва — Саратов",
                        "start": "2099-09-18T19:30:00+03:00",
                        "end": "2099-09-18T22:05:00+04:00",
                        "start_location": "Москва, аэропорт Шереметьево D",
                        "end_location": "Саратов, аэропорт Гагарин",
                        "ready": True,
                    },
                },
                {
                    "action_type": "calendar_event",
                    "title": "DP6866 Саратов — Москва",
                    "due_at": "2099-09-20T23:40:00+04:00",
                    "confidence": 0.98,
                    "source": {"subject": "Маршрут-квитанция", "attachment": "ticket-back.pdf"},
                    "ready": True,
                    "warnings": [],
                    "attachment_event": {
                        "title": "DP6866 Саратов — Москва",
                        "start": "2099-09-20T23:40:00+04:00",
                        "end": "2099-09-21T00:30:00+03:00",
                        "start_location": "Саратов, аэропорт Гагарин",
                        "end_location": "Москва, аэропорт Шереметьево D",
                        "ready": True,
                    },
                },
            ],
            "attachment_analysis": {
                "enabled": True,
                "detected": 2,
                "supported_found": 2,
                "analyzed": 2,
                "warnings": [],
            },
        }

        answer = answer_email_chat_request(42, "Разбери почту")

        build_plan.assert_called_once_with(42, "Разбери почту", include_attachments=True)
        self.assertIn("Почта → вложения", answer)
        self.assertIn("найдено 2", answer)
        self.assertIn("ticket-out.pdf", answer)
        self.assertIn("ticket-back.pdf", answer)
        self.assertIn("DP6865 Москва — Саратов", answer)
        self.assertIn("DP6866 Саратов — Москва", answer)
        self.assertIn("Шереметьево D", answer)
        self.assertIn("аэропорт Гагарин", answer)
        self.assertNotIn("Старая переписка", answer)

    @patch("modules.email_api.answer_email_query", return_value="Результат быстрого поиска")
    @patch("modules.email_api.build_email_plan")
    def test_direct_email_search_keeps_fast_text_search(self, build_plan, answer_query):
        answer = answer_email_chat_request(42, "Найди письмо от РЕСО")

        self.assertEqual(answer, "Результат быстрого поиска")
        build_plan.assert_not_called()
        answer_query.assert_called_once_with(42, "Найди письмо от РЕСО")

    @patch("modules.email_api.build_email_plan")
    def test_deep_chat_request_exposes_attachment_diagnostics_when_no_event_extracted(self, build_plan):
        build_plan.return_value = {
            "summary": "Во вложениях пока не удалось выделить точное событие.",
            "actions": [],
            "attachment_analysis": {
                "enabled": True,
                "detected": 2,
                "supported_found": 2,
                "analyzed": 2,
                "warnings": ["Не удалось надёжно разобрать одно из вложений."],
            },
        }

        answer = answer_email_chat_request(42, "Что важного в почте для планирования?")

        build_plan.assert_called_once_with(42, "Что важного в почте для планирования?", include_attachments=True)
        self.assertIn("найдено 2", answer)
        self.assertIn("поддерживается 2", answer)
        self.assertIn("разобрано 2", answer)
        self.assertIn("Не удалось надёжно разобрать", answer)


if __name__ == "__main__":
    unittest.main()
