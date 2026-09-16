from __future__ import annotations

import unittest
from unittest.mock import patch

from modules import email_actions_api


class EmailTicketAutoCalendarTests(unittest.TestCase):
    def _action(self, confidence: float = 0.99, document_type: str = "flight_ticket") -> dict:
        return {
            "action_type": "calendar_event",
            "title": "Рейс SU100 Санкт-Петербург — Москва",
            "confidence": confidence,
            "attachment_document_type": document_type,
            "ready": True,
            "source": {
                "account": "Gmail",
                "provider_message_id": "msg-1",
                "attachment_id": "att-1",
                "attachment": "ticket.pdf",
            },
            "attachment_event": {
                "title": "Рейс SU100 Санкт-Петербург — Москва",
                "start": "2099-09-27T21:00:00+03:00",
                "end": "2099-09-27T22:20:00+03:00",
                "start_timezone": "Europe/Moscow",
                "end_timezone": "Europe/Moscow",
                "start_location": "Санкт-Петербург, аэропорт Пулково",
                "end_location": "Москва, аэропорт Шереметьево",
                "movement": True,
                "timezone_verified": True,
                "location": "Санкт-Петербург, Пулково → Москва, Шереметьево",
                "category": "travel",
                "confidence": confidence,
                "ready": True,
                "warnings": [],
            },
            "warnings": [],
        }

    @patch("modules.email_actions_api._ensure_attachment_calendar_event")
    def test_transport_ticket_at_99_percent_is_auto_created(self, ensure):
        ensure.return_value = ({"id": "event-1"}, True)
        action = self._action(0.99)
        plan = {"actions": [action]}

        result = email_actions_api.apply_high_confidence_attachment_actions(42, plan)

        ensure.assert_called_once()
        self.assertTrue(action["applied"])
        self.assertTrue(action["auto_created"])
        self.assertEqual(action["calendar_event_id"], "event-1")
        self.assertEqual(result["auto_calendar"]["created"], 1)

    @patch("modules.email_actions_api._ensure_attachment_calendar_event")
    def test_ticket_below_99_percent_waits_for_button(self, ensure):
        action = self._action(0.98)
        plan = {"actions": [action]}

        result = email_actions_api.apply_high_confidence_attachment_actions(42, plan)

        ensure.assert_not_called()
        self.assertNotIn("applied", action)
        self.assertEqual(result["auto_calendar"]["created"], 0)

    @patch("modules.email_actions_api._ensure_attachment_calendar_event")
    def test_non_transport_ticket_does_not_auto_create(self, ensure):
        action = self._action(1.0, document_type="event_ticket")
        email_actions_api.apply_high_confidence_attachment_actions(42, {"actions": [action]})
        ensure.assert_not_called()

    @patch("modules.email_actions_api._ensure_attachment_calendar_event")
    def test_duplicate_ticket_is_marked_existing(self, ensure):
        ensure.return_value = ({"id": "event-existing"}, False)
        action = self._action(1.0)

        result = email_actions_api.apply_high_confidence_attachment_actions(42, {"actions": [action]})

        self.assertTrue(action["applied"])
        self.assertTrue(action["already_in_calendar"])
        self.assertNotIn("auto_created", action)
        self.assertEqual(result["auto_calendar"]["already_present"], 1)

    @patch("modules.email_actions_api._create_event")
    @patch("modules.email_actions_api._existing_attachment_calendar_event")
    def test_source_key_prevents_duplicate_calendar_insert(self, existing, create_event):
        existing.return_value = {"id": "event-existing", "status": "confirmed"}
        action = self._action(1.0)

        event, created = email_actions_api._ensure_attachment_calendar_event(
            42,
            action["attachment_event"],
            source=action["source"],
            auto_created=True,
        )

        self.assertFalse(created)
        self.assertEqual(event["id"], "event-existing")
        create_event.assert_not_called()
        key = existing.call_args.args[1]
        self.assertEqual(len(key), 40)


if __name__ == "__main__":
    unittest.main()
