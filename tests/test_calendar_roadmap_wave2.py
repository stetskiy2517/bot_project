from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from modules.calendar_actions import _build_update_patch, _extract_update_target, resume_pending_action
from modules.calendar_event_features import apply_event_features
from modules.calendar_recurrence import (
    recurring_scope_from_text,
    split_recurring_series_for_update,
    trim_recurring_series_from,
)
from modules.router import INTENT_UPDATE, detect_intent


class CalendarPropertyUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tz = "Europe/Moscow"
        self.event = {
            "id": "instance-1",
            "summary": "Встреча с Ивановым",
            "description": "AI Smart Planner category: work",
            "start": {"dateTime": "2026-09-14T10:00:00+03:00", "timeZone": self.tz},
            "end": {"dateTime": "2026-09-14T11:00:00+03:00", "timeZone": self.tz},
            "attendees": [{"email": "old@example.com"}],
        }

    def test_priority_is_saved_without_polluting_title(self):
        event = {
            "summary": "placeholder",
            "description": "AI Smart Planner category: work",
            "start": {"dateTime": "2026-09-14T10:00:00+03:00", "timeZone": self.tz},
            "end": {"dateTime": "2026-09-14T11:00:00+03:00", "timeZone": self.tz},
        }
        enriched = apply_event_features(event, "встреча в понедельник в 10 высокий приоритет")
        self.assertEqual(
            enriched["extendedProperties"]["private"]["smartPlannerPriority"],
            "high",
        )
        self.assertNotIn("приоритет", enriched["summary"].lower())

    def test_recurrence_can_end_on_named_date(self):
        event = {
            "summary": "placeholder",
            "description": "AI Smart Planner category: work",
            "start": {"dateTime": "2026-09-14T10:00:00+03:00", "timeZone": self.tz},
            "end": {"dateTime": "2026-09-14T11:00:00+03:00", "timeZone": self.tz},
        }
        enriched = apply_event_features(
            event,
            "встреча каждый понедельник в 10 до 1 декабря 2026",
        )
        self.assertIn("FREQ=WEEKLY", enriched["recurrence"][0])
        self.assertIn("UNTIL=", enriched["recurrence"][0])
        self.assertNotIn("до 1 декабря", enriched["summary"].lower())

    def test_location_reminder_category_priority_and_attendees_can_be_changed(self):
        location = _build_update_patch(
            self.event,
            "измени место встречи с Ивановым на Тверская 1",
            self.tz,
        )
        self.assertEqual(location["location"], "Тверская 1")

        reminder = _build_update_patch(
            self.event,
            "измени напоминание встречи с Ивановым за 30 минут",
            self.tz,
        )
        self.assertEqual(reminder["reminders"]["overrides"][0]["minutes"], 30)

        category = _build_update_patch(
            self.event,
            "поменяй категорию встречи с Ивановым на семья",
            self.tz,
            category_colors={"family": "4"},
        )
        self.assertEqual(category["colorId"], "4")
        self.assertIn("category: family", category["description"])

        priority = _build_update_patch(
            self.event,
            "сделай встречу с Ивановым с высоким приоритетом",
            self.tz,
        )
        self.assertEqual(
            priority["extendedProperties"]["private"]["smartPlannerPriority"],
            "high",
        )

        attendee = _build_update_patch(
            self.event,
            "добавь участника к встрече с Ивановым new@example.com",
            self.tz,
        )
        self.assertEqual(
            [item["email"] for item in attendee["attendees"]],
            ["old@example.com", "new@example.com"],
        )

    def test_property_command_target_cleanup(self):
        self.assertEqual(
            _extract_update_target("измени место встречи с Ивановым на Тверская 1"),
            "встречи с Ивановым",
        )
        self.assertEqual(
            _extract_update_target("добавь напоминание к встрече с Ивановым за 30 минут"),
            "встрече с Ивановым",
        )
        self.assertEqual(
            _extract_update_target("добавь участника к встрече с Ивановым new@example.com"),
            "встрече с Ивановым",
        )

    def test_property_commands_route_as_updates(self):
        cases = (
            "добавь напоминание к встрече за 30 минут",
            "убери повтор у встречи с Ивановым",
            "добавь участника к встрече new@example.com",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_UPDATE)


class CalendarRecurrenceHelpersTests(unittest.TestCase):
    def setUp(self):
        self.tz = "Europe/Moscow"
        self.instance = {
            "id": "instance-2",
            "recurringEventId": "series-1",
            "summary": "Планёрка",
            "originalStartTime": {"dateTime": "2026-09-21T10:00:00+03:00", "timeZone": self.tz},
            "start": {"dateTime": "2026-09-21T10:00:00+03:00", "timeZone": self.tz},
            "end": {"dateTime": "2026-09-21T11:00:00+03:00", "timeZone": self.tz},
        }
        self.parent = {
            "id": "series-1",
            "summary": "Планёрка",
            "start": {"dateTime": "2026-09-07T10:00:00+03:00", "timeZone": self.tz},
            "end": {"dateTime": "2026-09-07T11:00:00+03:00", "timeZone": self.tz},
            "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO"],
        }

    def _service(self):
        service = MagicMock()
        service.events.return_value.get.return_value.execute.return_value = self.parent
        service.events.return_value.patch.return_value.execute.return_value = self.parent
        service.events.return_value.insert.return_value.execute.return_value = {"id": "series-2", "summary": "Планёрка"}
        service.events.return_value.delete.return_value.execute.return_value = None
        return service

    def test_scope_phrases(self):
        self.assertEqual(recurring_scope_from_text("перенеси только эту встречу"), "this")
        self.assertEqual(recurring_scope_from_text("удали все будущие"), "future")
        self.assertEqual(recurring_scope_from_text("удали всю серию"), "series")

    def test_trim_future_instances_updates_parent_rrule(self):
        service = self._service()
        trim_recurring_series_from(service, self.instance, self.tz)
        patch_kwargs = service.events.return_value.patch.call_args.kwargs
        self.assertEqual(patch_kwargs["eventId"], "series-1")
        rule = patch_kwargs["body"]["recurrence"][0]
        self.assertIn("UNTIL=", rule)
        self.assertNotIn("COUNT=", rule)

    def test_split_future_series_inserts_new_series_then_trims_old(self):
        service = self._service()
        patch = {
            "start": {"dateTime": "2026-09-21T11:00:00+03:00", "timeZone": self.tz},
            "end": {"dateTime": "2026-09-21T12:00:00+03:00", "timeZone": self.tz},
        }
        result = split_recurring_series_for_update(service, self.instance, patch, self.tz)
        self.assertEqual(result["id"], "series-2")
        inserted = service.events.return_value.insert.call_args.kwargs["body"]
        self.assertEqual(inserted["start"]["dateTime"], "2026-09-21T11:00:00+03:00")
        self.assertEqual(inserted["recurrence"], self.parent["recurrence"])
        self.assertTrue(service.events.return_value.patch.called)


class CalendarRecurringDialogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        self.context = SimpleNamespace(user_data={})
        self.event = {
            "id": "instance-2",
            "recurringEventId": "series-1",
            "summary": "Планёрка",
            "start": {"dateTime": "2026-09-21T10:00:00+03:00"},
            "end": {"dateTime": "2026-09-21T11:00:00+03:00"},
            "originalStartTime": {"dateTime": "2026-09-21T10:00:00+03:00"},
        }

    async def test_recurring_delete_scope_is_requested(self):
        pending = {
            "type": "select_recurring_delete_scope",
            "event": self.event,
            "timezone": "Europe/Moscow",
        }
        self.context.user_data["smart_planner_pending"] = pending
        handled = await resume_pending_action(self.update, self.context, "всю серию", pending)
        self.assertTrue(handled)
        next_pending = self.context.user_data["smart_planner_pending"]
        self.assertEqual(next_pending["type"], "confirm_delete")
        self.assertEqual(next_pending["scope"], "series")

    async def test_confirm_delete_series_deletes_parent(self):
        pending = {
            "type": "confirm_delete",
            "event": self.event,
            "timezone": "Europe/Moscow",
            "scope": "series",
        }
        self.context.user_data["smart_planner_pending"] = pending
        service = MagicMock()
        service.events.return_value.delete.return_value.execute.return_value = None
        with patch("modules.calendar_actions._get_calendar_service", return_value=service):
            handled = await resume_pending_action(self.update, self.context, "да", pending)
        self.assertTrue(handled)
        self.assertEqual(service.events.return_value.delete.call_args.kwargs["eventId"], "series-1")


if __name__ == "__main__":
    unittest.main()
