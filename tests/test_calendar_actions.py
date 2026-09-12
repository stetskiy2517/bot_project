from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from modules.calendar_actions import (
    _build_update_patch,
    _duration_from_update,
    _event_matches_query,
    _extract_delete_query,
    _extract_update_target,
    _find_conflicts,
    _has_explicit_delete_period,
    _is_bulk_delete_request,
    _new_title_from_update,
    delete_from_text,
    resume_pending_action,
)
from modules.router import INTENT_UPDATE, detect_intent


class CalendarActionsTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("Europe/Moscow")
        self.event = {
            "id": "event-1",
            "summary": "Встреча с Ивановым",
            "start": {"dateTime": "2026-09-04T10:00:00+03:00"},
            "end": {"dateTime": "2026-09-04T11:00:00+03:00"},
        }

    def test_delete_query(self):
        self.assertEqual(_extract_delete_query("отмени врача завтра"), "врача")
        self.assertEqual(_extract_delete_query("удали встречу в пятницу"), "встречу")

    def test_bulk_delete_detection_requires_period_for_safety(self):
        self.assertTrue(_is_bulk_delete_request("удали все встречи завтра"))
        self.assertTrue(_has_explicit_delete_period("удали все встречи завтра"))
        self.assertTrue(_is_bulk_delete_request("удали все события"))
        self.assertFalse(_has_explicit_delete_period("удали все события"))

    def test_update_target(self):
        self.assertEqual(_extract_update_target("перенеси встречу на 16:00"), "встречу")
        self.assertEqual(_extract_update_target("перенеси врача на субботу"), "врача")
        self.assertEqual(_extract_update_target("сделай встречу на 2 часа"), "встречу")
        self.assertEqual(_extract_update_target("переименуй встречу в созвон с клиентом"), "встречу")

    def test_duration_and_title_changes(self):
        self.assertEqual(_duration_from_update("сделай встречу на 2 часа"), timedelta(hours=2))
        self.assertEqual(_duration_from_update("измени созвон на 30 минут"), timedelta(minutes=30))
        self.assertEqual(_new_title_from_update("переименуй встречу в созвон с клиентом"), "Созвон с клиентом")

    def test_time_update_preserves_duration(self):
        patch_body = _build_update_patch(self.event, "перенеси встречу на 16:00", "Europe/Moscow")
        self.assertEqual(patch_body["start"]["dateTime"], "2026-09-04T16:00:00+03:00")
        self.assertEqual(patch_body["end"]["dateTime"], "2026-09-04T17:00:00+03:00")

    def test_duration_update(self):
        patch_body = _build_update_patch(self.event, "сделай встречу на 2 часа", "Europe/Moscow")
        self.assertEqual(patch_body["end"]["dateTime"], "2026-09-04T12:00:00+03:00")

    def test_rename_update(self):
        patch_body = _build_update_patch(self.event, "переименуй встречу в созвон с клиентом", "Europe/Moscow")
        self.assertEqual(patch_body, {"summary": "Созвон с клиентом"})

    def test_query_matching_tolerates_russian_endings(self):
        self.assertTrue(_event_matches_query({"summary": "Прием у врача"}, "врача"))
        self.assertTrue(_event_matches_query({"summary": "Встреча с Ивановым"}, "Иванов"))
        self.assertFalse(_event_matches_query({"summary": "Совещание отдела"}, "невролог"))

    def test_conflicts_are_real_overlaps_and_can_exclude_current_event(self):
        events = [
            self.event,
            {
                "id": "event-2",
                "summary": "Другой созвон",
                "start": {"dateTime": "2026-09-04T10:30:00+03:00"},
                "end": {"dateTime": "2026-09-04T11:30:00+03:00"},
            },
        ]
        start = datetime(2026, 9, 4, 10, 15, tzinfo=self.zone)
        end = datetime(2026, 9, 4, 10, 45, tzinfo=self.zone)
        with patch("modules.calendar_actions._list_events", return_value=events):
            conflicts = _find_conflicts(1, start, end, exclude_event_id="event-1")
        self.assertEqual([event["id"] for event in conflicts], ["event-2"])

    def test_router_recognises_new_update_phrases(self):
        self.assertEqual(detect_intent("сделай встречу на 2 часа").name, INTENT_UPDATE)
        self.assertEqual(detect_intent("переименуй встречу в созвон").name, INTENT_UPDATE)


class CalendarBulkDeleteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.zone = ZoneInfo("Europe/Moscow")
        self.events = [
            {
                "id": "event-1",
                "summary": "Встреча с Ивановым",
                "start": {"dateTime": "2026-09-13T10:00:00+03:00"},
                "end": {"dateTime": "2026-09-13T11:00:00+03:00"},
            },
            {
                "id": "event-2",
                "summary": "Созвон с клиентом",
                "start": {"dateTime": "2026-09-13T14:00:00+03:00"},
                "end": {"dateTime": "2026-09-13T15:00:00+03:00"},
            },
        ]

    def _update(self):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )

    async def test_delete_all_tomorrow_asks_for_one_bulk_confirmation(self):
        update = self._update()
        context = SimpleNamespace(user_data={})
        start = datetime(2026, 9, 13, 0, 0, tzinfo=self.zone)
        end = start + timedelta(days=1)
        with patch("modules.calendar_actions.get_user_timezone", return_value="Europe/Moscow"), \
             patch("modules.calendar_actions._parse_search_period", return_value=(start, end)), \
             patch("modules.calendar_actions._list_events", return_value=self.events):
            handled = await delete_from_text(update, context, "удали все встречи завтра")

        self.assertTrue(handled)
        pending = context.user_data["smart_planner_pending"]
        self.assertEqual(pending["type"], "confirm_delete_many")
        self.assertEqual([event["id"] for event in pending["events"]], ["event-1", "event-2"])
        reply = update.message.reply_text.await_args.args[0]
        self.assertIn("Удалить все события", reply)
        self.assertIn("(2)", reply)

    async def test_bulk_delete_confirmation_deletes_every_selected_event(self):
        update = self._update()
        context = SimpleNamespace(user_data={"smart_planner_pending": {}})
        pending = {
            "type": "confirm_delete_many",
            "events": self.events,
            "timezone": "Europe/Moscow",
        }
        service = MagicMock()
        service.events.return_value.delete.return_value.execute.return_value = None
        with patch("modules.calendar_actions._get_calendar_service", return_value=service):
            handled = await resume_pending_action(update, context, "да", pending)

        self.assertTrue(handled)
        self.assertEqual(service.events.return_value.delete.call_count, 2)
        self.assertNotIn("smart_planner_pending", context.user_data)
        self.assertIn("Удалил все события", update.message.reply_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
