from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

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

    def test_source_key_is_stable_when_ai_changes_title_or_offset_spelling(self):
        action = self._action(1.0)
        first = dict(action["attachment_event"])
        second = dict(first)
        second["title"] = "Перелёт Пулково → Шереметьево"
        second["start"] = "2099-09-27T18:00:00Z"
        second["end"] = "2099-09-27T19:20:00Z"

        key_one = email_actions_api._attachment_event_key(action["source"], first)
        key_two = email_actions_api._attachment_event_key(action["source"], second)

        self.assertEqual(key_one, key_two)
        self.assertEqual(len(key_one or ""), 40)

    def test_same_flight_from_different_attachments_uses_same_event_key(self):
        first = self._action(1.0)
        second = self._action(1.0)
        second["source"] = {
            **second["source"],
            "attachment_id": "att-2",
            "attachment": "itinerary.pdf",
        }
        second["attachment_event"] = {
            **second["attachment_event"],
            "title": "Перелёт SU100 Пулково — Шереметьево",
        }

        key_one = email_actions_api._attachment_event_key(first["source"], first["attachment_event"])
        key_two = email_actions_api._attachment_event_key(second["source"], second["attachment_event"])

        self.assertEqual(key_one, key_two)

    @patch("modules.email_actions_api._calendar_service")
    def test_semantic_lookup_matches_same_flight_when_attachment_uses_boarding_time(self, calendar_service):
        service = MagicMock()
        calendar_service.return_value = service
        service.events.return_value.list.return_value.execute.return_value = {
            "items": [
                {
                    "id": "existing-flight",
                    "status": "confirmed",
                    "summary": "Рейс SU100 Санкт-Петербург — Москва",
                    "description": "Рейс SU100",
                    "start": {"dateTime": "2099-09-27T18:00:00Z"},
                    "end": {"dateTime": "2099-09-27T19:20:00Z"},
                    "location": "Пулково, Санкт-Петербург",
                    "extendedProperties": {
                        "private": {
                            "smartPlannerType": "email_attachment_import",
                            "smartPlannerStartLocation": "Пулково, Санкт-Петербург",
                            "smartPlannerEndLocation": "Шереметьево, Москва",
                        }
                    },
                }
            ]
        }
        proposal = {
            **self._action(1.0)["attachment_event"],
            "title": "Посадочный талон SU100",
            "start": "2099-09-27T18:35:00Z",
            "end": "2099-09-27T19:20:00Z",
            "start_location": "Санкт-Петербург, аэропорт Пулково, терминал 1",
            "end_location": "Москва, аэропорт Шереметьево",
        }

        existing = email_actions_api._existing_semantic_attachment_calendar_event(42, proposal)

        self.assertIsNotNone(existing)
        self.assertEqual(existing["id"], "existing-flight")

    @patch("modules.email_actions_api._calendar_service")
    def test_semantic_lookup_rejects_different_flight_number(self, calendar_service):
        service = MagicMock()
        calendar_service.return_value = service
        service.events.return_value.list.return_value.execute.return_value = {
            "items": [
                {
                    "id": "other-flight",
                    "status": "confirmed",
                    "summary": "Рейс SU200 Санкт-Петербург — Москва",
                    "description": "Рейс SU200",
                    "start": {"dateTime": "2099-09-27T18:00:00Z"},
                    "end": {"dateTime": "2099-09-27T19:20:00Z"},
                    "location": "Пулково, Санкт-Петербург",
                    "extendedProperties": {
                        "private": {
                            "smartPlannerType": "email_attachment_import",
                            "smartPlannerStartLocation": "Пулково, Санкт-Петербург",
                            "smartPlannerEndLocation": "Шереметьево, Москва",
                        }
                    },
                }
            ]
        }
        proposal = self._action(1.0)["attachment_event"]

        existing = email_actions_api._existing_semantic_attachment_calendar_event(42, proposal)

        self.assertIsNone(existing)

    @patch("modules.email_actions_api._create_event")
    @patch("modules.email_actions_api._existing_semantic_attachment_calendar_event")
    @patch("modules.email_actions_api._existing_attachment_calendar_event")
    def test_legacy_semantic_match_prevents_new_duplicate(self, exact, semantic, create_event):
        exact.return_value = None
        semantic.return_value = {"id": "legacy-existing", "status": "confirmed"}
        action = self._action(1.0)

        event, created = email_actions_api._ensure_attachment_calendar_event(
            42,
            action["attachment_event"],
            source=action["source"],
            auto_created=True,
        )

        self.assertFalse(created)
        self.assertEqual(event["id"], "legacy-existing")
        semantic.assert_called_once()
        create_event.assert_not_called()

    @patch("modules.email_actions_api._calendar_service")
    def test_semantic_lookup_matches_legacy_import_by_time_and_route(self, calendar_service):
        service = MagicMock()
        calendar_service.return_value = service
        service.events.return_value.list.return_value.execute.return_value = {
            "items": [
                {
                    "id": "legacy-event",
                    "status": "confirmed",
                    "start": {"dateTime": "2099-09-27T18:00:00Z"},
                    "end": {"dateTime": "2099-09-27T19:20:00Z"},
                    "location": "Пулково, Санкт-Петербург",
                    "extendedProperties": {
                        "private": {
                            "smartPlannerType": "email_attachment_import",
                            "smartPlannerStartLocation": "Пулково, Санкт-Петербург",
                            "smartPlannerEndLocation": "Шереметьево, Москва",
                        }
                    },
                }
            ]
        }
        action = self._action(1.0)

        existing = email_actions_api._existing_semantic_attachment_calendar_event(
            42, action["attachment_event"]
        )

        self.assertIsNotNone(existing)
        self.assertEqual(existing["id"], "legacy-event")

    @patch("modules.email_actions_api._create_event")
    @patch("modules.email_actions_api._existing_semantic_attachment_calendar_event")
    @patch("modules.email_actions_api._existing_attachment_calendar_event")
    def test_source_key_prevents_duplicate_calendar_insert(self, existing, semantic, create_event):
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
        semantic.assert_not_called()
        key = existing.call_args.args[1]
        self.assertEqual(len(key), 40)


if __name__ == "__main__":
    unittest.main()
