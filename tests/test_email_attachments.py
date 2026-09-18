from __future__ import annotations

import base64
from email.message import EmailMessage
import unittest
from unittest.mock import MagicMock, patch

from integrations import email_gmail, email_imap
from modules import email_actions, email_actions_api, email_attachments


class GmailAttachmentTests(unittest.TestCase):
    def test_attachment_metadata_is_exposed_without_content(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"partId": "0", "mimeType": "text/plain", "filename": "", "body": {"data": "SGk="}},
                {
                    "partId": "1",
                    "mimeType": "application/pdf",
                    "filename": "ticket.pdf",
                    "body": {"attachmentId": "att-1", "size": 1234},
                },
            ],
        }
        items = email_gmail._attachment_metadata(payload)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["filename"], "ticket.pdf")
        self.assertEqual(items[0]["attachment_id"], "att-1")
        self.assertNotIn("data", items[0])

    @patch("integrations.email_gmail._service")
    def test_fetch_attachment_decodes_gmail_attachment_api(self, service_factory):
        service = service_factory.return_value
        attachment_get = service.users.return_value.messages.return_value.attachments.return_value.get
        encoded = base64.urlsafe_b64encode(b"%PDF-ticket").decode("ascii").rstrip("=")
        attachment_get.return_value.execute.return_value = {"data": encoded}

        data = email_gmail.fetch_attachment_bytes(
            {"token": "x"},
            "message-1",
            {"attachment_id": "att-1", "size": 11},
            max_bytes=1024,
        )

        self.assertEqual(data, b"%PDF-ticket")
        attachment_get.assert_called_once_with(userId="me", messageId="message-1", id="att-1")


class ImapAttachmentTests(unittest.TestCase):
    def test_imap_attachment_can_be_extracted_from_in_memory_message(self):
        message = EmailMessage()
        message["From"] = "Airline <flight@example.com>"
        message["To"] = "user@example.com"
        message["Subject"] = "Ваш билет"
        message.set_content("Билет во вложении")
        message.add_attachment(b"%PDF-flight", maintype="application", subtype="pdf", filename="flight.pdf")
        raw = message.as_bytes()

        payload = email_imap._message_payload(raw)
        self.assertEqual(len(payload["attachments"]), 1)
        attachment = payload["attachments"][0]
        self.assertEqual(attachment["filename"], "flight.pdf")
        self.assertEqual(
            email_imap.extract_attachment_bytes(raw, attachment, max_bytes=1024),
            b"%PDF-flight",
        )
        self.assertEqual(payload["_raw_message"], raw)


class EmailAttachmentPlanningTests(unittest.TestCase):
    @patch("modules.email_attachments.analyze_file_bytes")
    @patch("modules.email_attachments.fetch_gmail_attachment_bytes", return_value=b"%PDF-ticket")
    @patch("modules.email_attachments.get_email_account")
    def test_flight_attachment_uses_shared_file_ingest_pipeline(self, account, fetch, analyze):
        account.return_value = {"credentials": {"token": {"access_token": "secret"}}}
        analyze.return_value = {
            "summary": "Авиабилет Москва — Саратов",
            "warnings": [],
            "events": [
                {
                    "title": "DP6865 Москва — Саратов",
                    "start": "2099-09-18T19:30:00+03:00",
                    "end": "2099-09-18T22:05:00+04:00",
                    "start_timezone": "Europe/Moscow",
                    "end_timezone": "Europe/Saratov",
                    "start_location": "Москва, аэропорт Шереметьево D",
                    "end_location": "Саратов, аэропорт Гагарин",
                    "movement": True,
                    "location": "Москва, Шереметьево D → Саратов, Гагарин",
                    "description": "Рейс DP6865",
                    "category": "travel",
                    "confidence": 0.99,
                    "ready": True,
                    "warnings": [],
                }
            ],
        }
        messages = [
            (
                {"account_id": 7, "provider": "gmail", "display_name": "Gmail"},
                {
                    "provider_message_id": "msg-1",
                    "from": "Победа <info@example.com>",
                    "subject": "Маршрут-квитанция",
                    "attachments": [
                        {"filename": "ticket.pdf", "mime_type": "application/pdf", "size": 1024, "attachment_id": "att-1"}
                    ],
                },
            )
        ]

        result = email_attachments.analyze_email_attachments(42, messages, user_timezone="Europe/Moscow")

        self.assertEqual(result["analyzed"], 1)
        self.assertEqual(len(result["actions"]), 1)
        action = result["actions"][0]
        self.assertEqual(action["action_type"], "calendar_event")
        self.assertEqual(action["source"]["attachment"], "ticket.pdf")
        self.assertTrue(action["ready"])
        self.assertTrue(action["attachment_event"]["movement"])
        self.assertEqual(action["attachment_event"]["end_timezone"], "Europe/Saratov")
        fetch.assert_called_once()
        analyze.assert_called_once_with(
            b"%PDF-ticket",
            filename="document.pdf",
            mimetype="application/pdf",
            user_timezone="Europe/Moscow",
        )

    @patch("modules.email_attachments.analyze_file_bytes")
    @patch("modules.email_attachments.fetch_gmail_attachment_bytes", side_effect=[b"%PDF-ticket", b"%PDF-receipt"])
    @patch("modules.email_attachments.get_email_account")
    def test_same_flight_in_ticket_and_itinerary_is_returned_once(self, account, _fetch, analyze):
        account.return_value = {"credentials": {"token": {"access_token": "secret"}}}
        analyze.side_effect = [
            {
                "document_type": "flight_ticket",
                "summary": "Билет DP6865 Москва — Саратов",
                "warnings": [],
                "events": [
                    {
                        "title": "Рейс DP6865 Москва — Саратов",
                        "start": "2099-09-18T19:30:00+03:00",
                        "end": "2099-09-18T22:05:00+04:00",
                        "start_timezone": "Europe/Moscow",
                        "end_timezone": "Europe/Saratov",
                        "start_location": "Москва, аэропорт Шереметьево",
                        "end_location": "Саратов, аэропорт Гагарин",
                        "movement": True,
                        "timezone_verified": True,
                        "location": "Москва, Шереметьево → Саратов, Гагарин",
                        "description": "Рейс DP6865",
                        "category": "travel",
                        "confidence": 0.99,
                        "ready": True,
                        "warnings": [],
                    }
                ],
            },
            {
                "document_type": "boarding_pass",
                "summary": "Маршрутная квитанция",
                "warnings": [],
                "events": [
                    {
                        "title": "Перелёт DP6865 Шереметьево — Гагарин",
                        "start": "2099-09-18T19:10:00+03:00",
                        "end": "2099-09-18T22:05:00+04:00",
                        "start_timezone": "Europe/Moscow",
                        "end_timezone": "Europe/Saratov",
                        "start_location": "Шереметьево, Москва, терминал D",
                        "end_location": "аэропорт Гагарин, Саратов",
                        "movement": True,
                        "timezone_verified": True,
                        "location": "Шереметьево → Гагарин",
                        "description": "DP6865",
                        "category": "travel",
                        "confidence": 1.0,
                        "ready": True,
                        "warnings": [],
                    }
                ],
            },
        ]
        messages = [
            (
                {"account_id": 7, "provider": "gmail", "display_name": "Gmail"},
                {
                    "provider_message_id": "msg-duplicate-flight",
                    "subject": "Билет и маршрутная квитанция",
                    "attachments": [
                        {"filename": "ticket.pdf", "mime_type": "application/pdf", "size": 1000, "attachment_id": "att-ticket"},
                        {"filename": "itinerary.pdf", "mime_type": "application/pdf", "size": 1000, "attachment_id": "att-route"},
                    ],
                },
            )
        ]

        result = email_attachments.analyze_email_attachments(42, messages, user_timezone="Europe/Moscow")

        self.assertEqual(result["analyzed"], 2)
        self.assertEqual(result["deduplicated"], 1)
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["attachment_event"]["title"], "Перелёт DP6865 Шереметьево — Гагарин")

    @patch("modules.email_attachments.analyze_file_bytes")
    @patch("modules.email_attachments.fetch_gmail_attachment_bytes", side_effect=[b"%PDF-one", b"%PDF-two"])
    @patch("modules.email_attachments.get_email_account")
    def test_different_flights_in_same_email_remain_separate(self, account, _fetch, analyze):
        account.return_value = {"credentials": {"token": {"access_token": "secret"}}}
        base = {
            "start_timezone": "Europe/Moscow",
            "end_timezone": "Europe/Moscow",
            "start_location": "Москва, Шереметьево",
            "end_location": "Санкт-Петербург, Пулково",
            "movement": True,
            "timezone_verified": True,
            "location": "Москва → Санкт-Петербург",
            "category": "travel",
            "confidence": 1.0,
            "ready": True,
            "warnings": [],
        }
        analyze.side_effect = [
            {
                "document_type": "flight_ticket",
                "summary": "Первый рейс",
                "warnings": [],
                "events": [{**base, "title": "SU100", "description": "Рейс SU100", "start": "2099-09-18T10:00:00+03:00", "end": "2099-09-18T11:30:00+03:00"}],
            },
            {
                "document_type": "flight_ticket",
                "summary": "Второй рейс",
                "warnings": [],
                "events": [{**base, "title": "SU200", "description": "Рейс SU200", "start": "2099-09-18T13:00:00+03:00", "end": "2099-09-18T14:30:00+03:00"}],
            },
        ]
        messages = [
            (
                {"account_id": 7, "provider": "gmail", "display_name": "Gmail"},
                {
                    "provider_message_id": "msg-two-flights",
                    "attachments": [
                        {"filename": "one.pdf", "mime_type": "application/pdf", "size": 1000, "attachment_id": "att-1"},
                        {"filename": "two.pdf", "mime_type": "application/pdf", "size": 1000, "attachment_id": "att-2"},
                    ],
                },
            )
        ]

        result = email_attachments.analyze_email_attachments(42, messages, user_timezone="Europe/Moscow")

        self.assertEqual(result["deduplicated"], 0)
        self.assertEqual(len(result["actions"]), 2)

    @patch("modules.email_attachments.analyze_file_bytes")
    @patch("modules.email_attachments.fetch_gmail_attachment_bytes")
    def test_oversized_attachment_is_skipped_before_fetch(self, fetch, analyze):
        messages = [
            (
                {"account_id": 7, "provider": "gmail"},
                {
                    "provider_message_id": "msg-1",
                    "attachments": [
                        {
                            "filename": "huge.pdf",
                            "mime_type": "application/pdf",
                            "size": email_attachments.MAX_DOCUMENT_BYTES + 1,
                            "attachment_id": "att-1",
                        }
                    ],
                },
            )
        ]

        result = email_attachments.analyze_email_attachments(42, messages, user_timezone="Europe/Moscow")

        self.assertEqual(result["analyzed"], 0)
        self.assertTrue(result["warnings"])
        fetch.assert_not_called()
        analyze.assert_not_called()

    @patch("modules.email_actions.analyze_email_attachments")
    @patch("modules.email_actions.complete_structured")
    @patch("modules.email_actions._collect")
    @patch("modules.email_actions.is_ai_available", return_value=True)
    @patch("modules.email_actions.has_ai_access", return_value=True)
    @patch("modules.email_actions.list_email_accounts")
    def test_background_email_plan_does_not_analyze_attachments_by_default(
        self, accounts, _access, _available, collect, complete, attachment_analysis
    ):
        accounts.return_value = [{"account_id": 1, "enabled": True}]
        collect.return_value = [({"provider": "gmail"}, {"subject": "Test", "body": "Body", "date": "2099-01-01T00:00:00+03:00"})]
        complete.return_value = {"summary": "ok", "actions": [], "draft_reply": None}

        result = email_actions.build_email_plan(1, "review")

        self.assertEqual(result["summary"], "ok")
        attachment_analysis.assert_not_called()

    @patch("modules.email_actions.get_user_timezone", return_value="Europe/Moscow")
    @patch("modules.email_actions.analyze_email_attachments")
    @patch("modules.email_actions.complete_structured")
    @patch("modules.email_actions._collect")
    @patch("modules.email_actions.is_ai_available", return_value=True)
    @patch("modules.email_actions.has_ai_access", return_value=True)
    @patch("modules.email_actions.list_email_accounts")
    def test_interactive_email_plan_merges_attachment_event(
        self, accounts, _access, _available, collect, complete, attachment_analysis, _timezone
    ):
        accounts.return_value = [{"account_id": 1, "enabled": True}]
        collect.return_value = [({"provider": "gmail"}, {"subject": "Ticket", "body": "See attachment", "date": "2099-01-01T00:00:00+03:00"})]
        complete.return_value = {"summary": "Письмо с билетом", "actions": [], "draft_reply": None}
        attachment_action = {
            "action_type": "calendar_event",
            "title": "Рейс",
            "due_at": "2099-09-18T19:30:00+03:00",
            "duration_minutes": 95,
            "confidence": 0.99,
            "reason": "ticket",
            "source": {"index": 1, "attachment": "ticket.pdf"},
            "attachment_event": {"title": "Рейс", "ready": True},
            "ready": True,
            "warnings": [],
        }
        attachment_analysis.return_value = {
            "actions": [attachment_action], "warnings": [], "analyzed": 1, "supported_found": 1
        }

        result = email_actions.build_email_plan(1, "review", include_attachments=True)

        self.assertEqual(result["actions"], [attachment_action])
        self.assertEqual(result["attachment_analysis"]["analyzed"], 1)
        attachment_analysis.assert_called_once()


class EmailAttachmentCalendarCreationTests(unittest.TestCase):
    @patch("modules.email_actions_api.get_category_colors", return_value={"travel": "7"})
    @patch("modules.email_actions_api._create_event")
    def test_rich_attachment_event_preserves_movement_metadata(self, create_event, _colors):
        create_event.side_effect = lambda _user_id, event: {"id": "calendar-1", **event}
        proposal = {
            "title": "DP6865 Москва — Саратов",
            "start": "2099-09-18T19:30:00+03:00",
            "end": "2099-09-18T22:05:00+04:00",
            "start_timezone": "Europe/Moscow",
            "end_timezone": "Europe/Saratov",
            "start_location": "Москва, аэропорт Шереметьево D",
            "end_location": "Саратов, аэропорт Гагарин",
            "movement": True,
            "location": "Москва → Саратов",
            "description": "Рейс DP6865",
            "category": "travel",
            "ready": True,
        }

        created = email_actions_api._create_attachment_calendar_event(5, proposal)

        self.assertEqual(created["id"], "calendar-1")
        event = create_event.call_args.args[1]
        self.assertEqual(event["location"], "Москва, аэропорт Шереметьево D")
        self.assertEqual(event["start"]["timeZone"], "Europe/Moscow")
        self.assertEqual(event["end"]["timeZone"], "Europe/Saratov")
        private = event["extendedProperties"]["private"]
        self.assertEqual(private["smartPlannerMovement"], "1")
        self.assertEqual(private["smartPlannerStartLocation"], "Москва, аэропорт Шереметьево D")
        self.assertEqual(private["smartPlannerEndLocation"], "Саратов, аэропорт Гагарин")

    def test_unready_attachment_event_cannot_be_created(self):
        with self.assertRaisesRegex(ValueError, "требует проверки"):
            email_actions_api._create_attachment_calendar_event(5, {"ready": False, "title": "Рейс"})


if __name__ == "__main__":
    unittest.main()
