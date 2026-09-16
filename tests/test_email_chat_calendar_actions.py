from __future__ import annotations

import unittest
from unittest.mock import patch

from modules.email_api import answer_email_chat_request_details


class EmailChatCalendarActionTests(unittest.TestCase):
    @patch("modules.email_api.apply_high_confidence_attachment_actions")
    @patch("modules.email_api.build_email_plan")
    def test_deep_email_chat_returns_structured_plan_for_buttons(self, build_plan, auto_apply):
        plan = {
            "summary": "Билет найден",
            "actions": [{
                "action_type": "calendar_event",
                "title": "Рейс SU100",
                "confidence": 0.98,
                "ready": True,
                "source": {"attachment": "ticket.pdf"},
                "attachment_event": {
                    "title": "Рейс SU100",
                    "start": "2099-09-27T21:00:00+03:00",
                    "end": "2099-09-27T22:20:00+03:00",
                    "ready": True,
                },
            }],
            "attachment_analysis": {"enabled": True, "detected": 1, "supported_found": 1, "analyzed": 1},
        }
        build_plan.return_value = plan
        auto_apply.side_effect = lambda _user, value: value

        answer, structured = answer_email_chat_request_details(42, "Разбери почту")

        build_plan.assert_called_once_with(42, "Разбери почту", include_attachments=True)
        auto_apply.assert_called_once_with(42, plan)
        self.assertIs(structured, plan)
        self.assertIn("Рейс SU100", answer)
        self.assertIn("кнопку «Добавить в календарь»", answer)


if __name__ == "__main__":
    unittest.main()
