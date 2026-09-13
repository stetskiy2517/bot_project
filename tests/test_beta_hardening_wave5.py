from datetime import datetime
import unittest
from unittest.mock import AsyncMock, patch

from modules.calendar import _extract_time
from modules.calendar_actions import (
    _extract_delete_query,
    _extract_update_target,
    _repair_action_text,
    resume_pending_action,
)
from modules.calendar_event_features import _recurrence_rule
from modules.reminders import (
    REMINDER_CREATE,
    _matches,
    _reminder_due_at,
    detect_reminder_intent,
    resume_pending_reminder,
)
from modules.router import (
    INTENT_DELETE,
    INTENT_UPDATE,
    _force_conflict_reply,
    detect_intent,
)


class TypoAndVoiceActionRoutingTests(unittest.TestCase):
    def test_common_action_typos_do_not_turn_into_new_events(self):
        cases = {
            "удоли встречу с Иваном завтра": INTENT_DELETE,
            "удолить созвон с Петей завтра": INTENT_DELETE,
            "перинеси встречу с Иваном на завтра 14": INTENT_UPDATE,
            "измини встречу с Иваном": INTENT_UPDATE,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, expected)

    def test_action_target_cleanup_understands_same_typos(self):
        self.assertEqual(_extract_delete_query("удоли встречу с Иваном завтра"), "встречу с Иваном")
        self.assertEqual(_extract_update_target("перинеси встречу с Иваном на завтра 14"), "встречу с Иваном")

    def test_move_command_repairs_voice_dropped_preposition_before_hour(self):
        repaired = _repair_action_text("перенеси встречу с Иваном на завтра 14")
        self.assertEqual(_extract_time(repaired), (14, 0))
        self.assertIn("в 14", repaired)


class ReminderVoiceHardeningTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 13, 12, 0)

    def test_reminder_with_dropped_preposition_keeps_explicit_hour(self):
        cases = {
            "напомни завтра 9 позвонить маме": datetime(2026, 9, 14, 9, 0),
            "завтра 18 напомни купить молоко": datetime(2026, 9, 14, 18, 0),
            "поставь напоминание послезавтра 7 выпить таблетку": datetime(2026, 9, 15, 7, 0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(detect_reminder_intent(text), REMINDER_CREATE)
                self.assertEqual(
                    _reminder_due_at(text, "Europe/Moscow", now=self.now).replace(tzinfo=None),
                    expected,
                )

    def test_natural_delete_query_tolerates_preposition_and_russian_case(self):
        reminder = {"text": "Позвонить маме"}
        self.assertTrue(_matches(reminder, "про маму"))
        self.assertTrue(_matches({"text": "Оплатить садик"}, "про оплату садика"))


class RecurrenceVoiceHardeningTests(unittest.TestCase):
    def test_spoken_week_intervals(self):
        cases = {
            "тренировка каждые две недели в субботу в 10": "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=SA",
            "созвон раз в две недели по понедельникам в 9": "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO",
            "врач каждые три недели в пятницу в 18": "RRULE:FREQ=WEEKLY;INTERVAL=3;BYDAY=FR",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_recurrence_rule(text), expected)


class ConflictReplyHardeningTests(unittest.TestCase):
    def test_natural_force_conflict_replies(self):
        for text in ["оставь как есть", "ставь как есть", "всё равно ставь", "ставь всё равно"]:
            with self.subTest(text=text):
                self.assertTrue(_force_conflict_reply(text))


class VoiceChoiceHardeningTests(unittest.IsolatedAsyncioTestCase):
    class Context:
        def __init__(self, pending):
            self.user_data = {"smart_planner_pending": pending}

    class Message:
        def __init__(self):
            self.replies = []

        async def reply_text(self, text):
            self.replies.append(text)

    class User:
        id = 1

    class Update:
        def __init__(self):
            self.message = VoiceChoiceHardeningTests.Message()
            self.effective_user = VoiceChoiceHardeningTests.User()

    async def test_event_choice_accepts_spoken_ordinal(self):
        events = [{"id": "one", "summary": "Первая"}, {"id": "two", "summary": "Вторая"}]
        pending = {
            "type": "select_delete",
            "events": events,
            "timezone": "Europe/Moscow",
            "text": "удали встречу",
        }
        context = self.Context(pending)
        update = self.Update()
        with patch("modules.calendar_actions._prepare_delete_confirmation", new=AsyncMock(return_value=True)) as prepare:
            handled = await resume_pending_action(update, context, "вторую")
        self.assertTrue(handled)
        prepare.assert_awaited_once()
        self.assertEqual(prepare.await_args.args[2]["id"], "two")

    async def test_reminder_choice_accepts_spoken_ordinal(self):
        reminders = [
            {"reminder_id": 1, "text": "Первое", "remind_at": "2026-09-14T09:00:00+03:00"},
            {"reminder_id": 2, "text": "Второе", "remind_at": "2026-09-14T10:00:00+03:00"},
        ]
        pending = {
            "type": "reminder_select_delete",
            "reminders": reminders,
            "timezone": "Europe/Moscow",
        }
        context = self.Context(pending)
        update = self.Update()
        with patch("modules.reminders.delete_reminder", return_value=True) as delete:
            handled = await resume_pending_reminder(update, context, "второе", pending)
        self.assertTrue(handled)
        delete.assert_called_once_with(1, 2)


if __name__ == "__main__":
    unittest.main()
