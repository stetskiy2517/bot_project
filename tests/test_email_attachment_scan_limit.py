from __future__ import annotations

import unittest
from unittest.mock import patch

from modules import email_attachments


class EmailAttachmentScanLimitTests(unittest.TestCase):
    @patch("modules.email_attachments.analyze_file_bytes")
    @patch("modules.email_attachments._fetch_bytes")
    def test_seven_supported_attachments_in_one_message_are_all_analyzed(self, fetch_bytes, analyze_file):
        attachments = [
            {
                "filename": f"file-{index}.pdf",
                "mime_type": "application/pdf",
                "size": 1024,
                "attachment_id": f"att-{index}",
            }
            for index in range(1, 8)
        ]
        messages = [
            (
                {"account_id": 7, "provider": "gmail", "display_name": "Gmail"},
                {
                    "provider_message_id": "msg-1",
                    "subject": "Билеты туда и обратно",
                    "attachments": attachments,
                },
            )
        ]
        fetch_bytes.side_effect = [f"doc-{index}".encode() for index in range(1, 8)]

        def analyze(content, **_kwargs):
            if content != b"doc-7":
                return {"document_type": "other", "summary": "Служебное вложение", "warnings": [], "events": []}
            return {
                "document_type": "flight_ticket",
                "summary": "Второй авиабилет",
                "warnings": [],
                "events": [
                    {
                        "title": "Перелёт Москва — Саратов",
                        "start": "2099-09-30T18:00:00+03:00",
                        "end": "2099-09-30T20:10:00+04:00",
                        "start_timezone": "Europe/Moscow",
                        "end_timezone": "Europe/Saratov",
                        "start_location": "Москва, аэропорт Шереметьево",
                        "end_location": "Саратов, аэропорт Гагарин",
                        "movement": True,
                        "location": "Москва → Саратов",
                        "description": "",
                        "category": "travel",
                        "confidence": 1.0,
                        "ready": True,
                        "warnings": [],
                    }
                ],
            }

        analyze_file.side_effect = analyze

        result = email_attachments.analyze_email_attachments(
            42,
            messages,
            user_timezone="Europe/Moscow",
        )

        self.assertEqual(result["detected"], 7)
        self.assertEqual(result["supported_found"], 7)
        self.assertEqual(result["analyzed"], 7)
        self.assertEqual(fetch_bytes.call_count, 7)
        self.assertEqual(analyze_file.call_count, 7)
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["source"]["attachment"], "file-7.pdf")
        self.assertFalse(any("максимум" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
