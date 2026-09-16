from __future__ import annotations

from email.message import EmailMessage
import unittest
from unittest.mock import patch

from integrations import email_gmail, email_imap
from modules import email_actions, email_attachments
from modules.email import _analysis_prompt, _format_messages


class AttachmentMetadataDetectionTests(unittest.TestCase):
    def test_gmail_detects_unnamed_pdf_attachment_by_mime(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "partId": "1",
                    "mimeType": "application/pdf",
                    "filename": "",
                    "headers": [{"name": "Content-Disposition", "value": "attachment"}],
                    "body": {"attachmentId": "att-pdf", "size": 1200},
                }
            ],
        }

        items = email_gmail._attachment_metadata(payload)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["filename"], "attachment.pdf")
        self.assertEqual(items[0]["mime_type"], "application/pdf")
        self.assertEqual(items[0]["attachment_id"], "att-pdf")

    def test_gmail_detects_document_attachment_even_without_disposition(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "partId": "2",
                    "mimeType": "application/pdf",
                    "filename": "",
                    "body": {"attachmentId": "att-doc", "size": 900},
                }
            ],
        }

        items = email_gmail._attachment_metadata(payload)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["filename"], "attachment.pdf")

    def test_imap_detects_attachment_without_filename(self):
        message = EmailMessage()
        message["From"] = "Airline <flight@example.com>"
        message["To"] = "user@example.com"
        message["Subject"] = "Ticket"
        message.set_content("See attachment")
        message.add_attachment(b"%PDF-ticket", maintype="application", subtype="pdf")

        payload = email_imap._message_payload(message.as_bytes())

        self.assertEqual(len(payload["attachments"]), 1)
        self.assertEqual(payload["attachments"][0]["filename"], "attachment.pdf")
        self.assertEqual(payload["attachments"][0]["mime_type"], "application/pdf")

    def test_file_ingest_falls_back_to_mime_when_extension_is_missing(self):
        detected = email_attachments._attachment_type("route-receipt", "application/pdf")

        self.assertIsNotNone(detected)
        name, mimetype, _limit, suffix = detected
        self.assertEqual(name, "route-receipt")
        self.assertEqual(mimetype, "application/pdf")
        self.assertEqual(suffix, ".pdf")


class AttachmentVisibilityTests(unittest.TestCase):
    def test_regular_email_output_shows_attachment_names(self):
        messages = [
            (
                {"display_name": "Gmail"},
                {
                    "subject": "Маршрут-квитанция",
                    "from": "Airline <mail@example.com>",
                    "date": "2099-09-16T10:00:00+03:00",
                    "body": "Ваш билет оформлен.",
                    "attachments": [{"filename": "ticket.pdf", "mime_type": "application/pdf"}],
                },
            )
        ]

        text = _format_messages(1, messages)

        self.assertIn("Вложения: ticket.pdf", text)

    @patch("modules.email._user_zone")
    def test_ai_email_prompt_marks_attachment_as_metadata(self, user_zone):
        from zoneinfo import ZoneInfo

        user_zone.return_value = ZoneInfo("Europe/Moscow")
        messages = [
            (
                {"display_name": "Gmail"},
                {
                    "subject": "Flight",
                    "from": "Airline",
                    "date": "2099-09-16T10:00:00+03:00",
                    "body": "See attached ticket",
                    "attachments": [{"filename": "ticket.pdf"}],
                },
            )
        ]

        prompt = _analysis_prompt(1, "Что в письме?", messages)

        self.assertIn("Вложения: ticket.pdf", prompt)


class WiderAttachmentScanTests(unittest.TestCase):
    @patch("modules.email_actions._read_account")
    @patch("modules.email_actions.list_email_accounts")
    def test_collect_honors_explicit_attachment_scan_limit(self, accounts, read_account):
        accounts.return_value = [{"account_id": 1, "enabled": True}]
        read_account.return_value = [
            {"date": f"2099-09-{day:02d}T10:00:00+03:00", "subject": f"Message {day}"}
            for day in range(1, 13)
        ]

        collected = email_actions._collect(1, limit=20)

        self.assertEqual(len(collected), 12)
        self.assertEqual(read_account.call_args.kwargs["limit"], 20)

    @patch("modules.email_actions.get_user_timezone", return_value="Europe/Moscow")
    @patch("modules.email_actions.analyze_email_attachments")
    @patch("modules.email_actions.complete_structured")
    @patch("modules.email_actions._collect")
    @patch("modules.email_actions.is_ai_available", return_value=True)
    @patch("modules.email_actions.has_ai_access", return_value=True)
    @patch("modules.email_actions.list_email_accounts")
    def test_interactive_plan_scans_twenty_but_prompts_with_six(
        self,
        accounts,
        _access,
        _available,
        collect,
        complete,
        analyze,
        _timezone,
    ):
        accounts.return_value = [{"account_id": 1, "enabled": True}]
        collected = [
            (
                {"account_id": 1, "provider": "gmail"},
                {
                    "date": f"2099-09-{20 - index:02d}T10:00:00+03:00",
                    "subject": f"Message {index}",
                    "body": "Body",
                    "attachments": [{"filename": "ticket.pdf"}] if index == 9 else [],
                },
            )
            for index in range(10)
        ]
        collect.return_value = collected
        analyze.return_value = {
            "actions": [],
            "warnings": [],
            "detected": 1,
            "analyzed": 1,
            "supported_found": 1,
        }
        complete.return_value = {"summary": "ok", "actions": [], "draft_reply": None}

        result = email_actions.build_email_plan(1, "review", include_attachments=True)

        collect.assert_called_once_with(1, limit=email_actions.ATTACHMENT_SCAN_MESSAGES)
        self.assertEqual(len(analyze.call_args.args[1]), 10)
        prompt = complete.call_args.args[0][1]["content"]
        self.assertIn("ПИСЬМО 6", prompt)
        self.assertNotIn("ПИСЬМО 7", prompt)
        self.assertEqual(result["attachment_analysis"]["detected"], 1)


if __name__ == "__main__":
    unittest.main()
