from datetime import datetime, time, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from modules.calendar_actions import _free_choice_title, resume_pending_action
from modules.calendar_availability import (
    _availability_period,
    _direct_booking_title,
    _ordinal_index,
)
from modules.calendar_user import _parse_view_window
from modules.router import INTENT_CREATE, INTENT_VIEW, _creation_text, detect_intent


class CalendarDialogueRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tz = "Europe/Moscow"
        self.zone = ZoneInfo(self.tz)
        self.now = datetime(2026, 9, 12, 12, 0, tzinfo=self.zone)  # Saturday

    def test_screenshot_view_phrase_is_recognised(self):
        self.assertEqual(detect_intent("Что мне сегодня в 21:00?").name, INTENT_VIEW)

    def test_screenshot_reminder_phrase_is_recognised(self):
        text = "Напомню, в 10 вечера выпить таблетку."
        self.assertEqual(detect_intent(text).name, INTENT_CREATE)
        self.assertTrue(_creation_text(text).lower().startswith("напомни"))

    def test_exact_time_view_window(self):
        start, end, label = _parse_view_window("что у меня в понедельник в 14:00?", self.tz, self.now)
        self.assertEqual(start, datetime(2026, 9, 14, 14, 0, tzinfo=self.zone))
        self.assertEqual(end, datetime(2026, 9, 14, 14, 1, tzinfo=self.zone))
        self.assertEqual(label, "понедельник в 14:00")

    def test_view_time_range_and_bounds(self):
        start, end, label = _parse_view_window("что у меня завтра с 14:00 до 17:00", self.tz, self.now)
        self.assertEqual(start, datetime(2026, 9, 13, 14, 0, tzinfo=self.zone))
        self.assertEqual(end, datetime(2026, 9, 13, 17, 0, tzinfo=self.zone))
        self.assertIn("с 14:00 до 17:00", label)

        start, end, label = _parse_view_window("что у меня завтра после 16:00", self.tz, self.now)
        self.assertEqual(start.hour, 16)
        self.assertEqual(end.date().isoformat(), "2026-09-14")
        self.assertIn("после 16:00", label)

        start, end, label = _parse_view_window("что у меня завтра до 12:00", self.tz, self.now)
        self.assertEqual(start.hour, 0)
        self.assertEqual(end.hour, 12)
        self.assertIn("до 12:00", label)

    def test_availability_respects_explicit_clock_bounds(self):
        start, end, _ = _availability_period("найди окно завтра между 14:00 и 17:00", self.tz, self.now)
        self.assertEqual(start, datetime(2026, 9, 13, 14, 0, tzinfo=self.zone))
        self.assertEqual(end, datetime(2026, 9, 13, 17, 0, tzinfo=self.zone))

    def test_direct_free_slot_booking_parsing(self):
        text = "поставь встречу с Ивановым в первое свободное окно завтра"
        self.assertEqual(_direct_booking_title(text), "встречу с Ивановым")
        self.assertEqual(_ordinal_index(text), 0)
        self.assertEqual(_ordinal_index("поставь на второй вариант"), 1)
        self.assertEqual(_free_choice_title("поставь встречу на второй вариант"), "встречу")


class CalendarDialoguePendingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.zone = ZoneInfo("Europe/Moscow")
        self.update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )

    async def test_conflict_can_choose_second_alternative(self):
        first = (
            datetime(2026, 9, 14, 15, 0, tzinfo=self.zone),
            datetime(2026, 9, 14, 16, 0, tzinfo=self.zone),
        )
        second = (
            datetime(2026, 9, 14, 16, 0, tzinfo=self.zone),
            datetime(2026, 9, 14, 17, 0, tzinfo=self.zone),
        )
        pending = {
            "type": "confirm_create_conflict",
            "timezone": "Europe/Moscow",
            "alternatives": [first, second],
            "event": {
                "summary": "Встреча",
                "start": {"dateTime": "2026-09-14T14:00:00+03:00", "timeZone": "Europe/Moscow"},
                "end": {"dateTime": "2026-09-14T15:00:00+03:00", "timeZone": "Europe/Moscow"},
            },
        }
        context = SimpleNamespace(user_data={"smart_planner_pending": pending})
        with patch("modules.calendar_actions._create_event") as create_event:
            handled = await resume_pending_action(self.update, context, "поставь на второй вариант", pending)

        self.assertTrue(handled)
        created = create_event.call_args.args[1]
        self.assertEqual(created["start"]["dateTime"], second[0].isoformat())
        self.assertEqual(created["end"]["dateTime"], second[1].isoformat())
        self.assertNotIn("smart_planner_pending", context.user_data)

    async def test_free_slot_choice_can_collect_title_then_create(self):
        slot = (
            datetime(2026, 9, 14, 15, 0, tzinfo=self.zone),
            datetime(2026, 9, 14, 16, 0, tzinfo=self.zone),
        )
        pending = {
            "type": "free_slot_choice",
            "timezone": "Europe/Moscow",
            "slots": [slot],
        }
        context = SimpleNamespace(user_data={"smart_planner_pending": pending})
        handled = await resume_pending_action(self.update, context, "займи это окно", pending)
        self.assertTrue(handled)
        self.assertEqual(context.user_data["smart_planner_pending"]["type"], "free_slot_title")

        pending_title = context.user_data["smart_planner_pending"]
        with patch("modules.calendar_actions.create_event_in_slot", return_value={"summary": "Созвон"}) as create:
            handled = await resume_pending_action(self.update, context, "Созвон", pending_title)
            create.assert_not_called()
            pending_confirm = context.user_data["smart_planner_pending"]
            self.assertEqual(pending_confirm["type"], "free_slot_confirm")
            await resume_pending_action(self.update, context, "да", pending_confirm)
        self.assertTrue(handled)
        create.assert_called_once()
        self.assertNotIn("smart_planner_pending", context.user_data)


if __name__ == "__main__":
    unittest.main()
