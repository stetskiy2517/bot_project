from __future__ import annotations

import unittest
from unittest.mock import patch

from modules import email_attachments


class EmailAttachmentIdentityTests(unittest.TestCase):
    @patch("modules.email_attachments.analyze_file_bytes")
    @patch("modules.email_attachments.fetch_gmail_attachment_bytes", return_value=b"%PDF-ticket")
    @patch("modules.email_attachments.get_email_account")
    def test_attachment_identity_and_document_type_reach_calendar_action(self, account, _fetch, analyze):
        account.return_value = {"credentials": {"token": {"access_token": "secret"}}}
        analyze.return_value = {
            "document_type": "flight_ticket",
            "summary": "Авиабилет",
            "warnings": [],
            "events": [{
                "title": "Рейс SU100",
                "start": "2099-09-27T21:00:00+03:00",
                "end": "2099-09-27T22:20:00+03:00",
                "start_timezone": "Europe/Moscow",
                "end_timezone": "Europe/Moscow",
                "start_location": "Санкт-Петербург, аэропорт Пулково",
                "end_location": "Москва, аэропорт Шереметьево",
                "movement": True,
                "timezone_verified": True,
                "location": "Санкт-Петербург → Москва",
                "category": "travel",
                "confidence": 1.0,
                "ready": True,
                "warnings": [],
            }],
        }
        messages = [(
            {"account_id": 7, "provider": "gmail", "display_name": "Gmail"},
            {
                "provider_message_id": "message-425",
                "subject": "FW: заказ билетов",
                "attachments": [{
                    "filename": "Маршрутная квитанция.pdf",
                    "mime_type": "application/pdf",
                    "size": 1024,
                    "attachment_id": "attachment-425",
                }],
            },
        )]

        result = email_attachments.analyze_email_attachments(42, messages, user_timezone="Europe/Moscow")

        action = result["actions"][0]
        self.assertEqual(action["attachment_document_type"], "flight_ticket")
        self.assertEqual(action["source"]["provider_message_id"], "message-425")
        self.assertEqual(action["source"]["attachment_id"], "attachment-425")
        self.assertEqual(action["source"]["attachment"], "Маршрутная квитанция.pdf")


if __name__ == "__main__":
    unittest.main()
