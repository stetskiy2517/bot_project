from datetime import datetime, timedelta
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from modules.calendar import _extract_duration, _extract_time, _extract_title, _parse_datetime, _parse_event_timing
from modules.calendar_actions import create_from_text
from modules.calendar_event_features import _recurrence_rule, build_all_day_event, is_all_day
from modules.router import INTENT_CREATE, INTENT_UNKNOWN, _needs_time, detect_intent


class HardUserParserWave2Tests(unittest.TestCase):
    def setUp(self):
        # Saturday, 12 September 2026, 17:00
        self.now = datetime(2026, 9, 12, 17, 0)

    def test_long_relative_offsets_without_clock_use_exact_offset(self):
        self.assertEqual(
            _parse_datetime("напомни через 2 дня позвонить маме", self.now),
            datetime(2026, 9, 14, 17, 0),
        )
        self.assertEqual(
            _parse_datetime("напомни через неделю оплатить счет", self.now),
            datetime(2026, 9, 19, 17, 0),
        )
        self.assertFalse(_needs_time("напомни через 2 дня позвонить маме"))

    def test_late_evening_hours_in_words(self):
        cases = {
            "созвон завтра в двадцать один": datetime(2026, 9, 13, 21, 0),
            "рейс завтра в двадцать два": datetime(2026, 9, 13, 22, 0),
            "такси завтра в двадцать три": datetime(2026, 9, 13, 23, 0),
            "встреча завтра в восемь тридцать": datetime(2026, 9, 13, 8, 30),
            "созвон завтра в двадцать один тридцать": datetime(2026, 9, 13, 21, 30),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_parse_datetime(text, self.now), expected)

    def test_more_common_typos_still_route(self):
        cases = [
            "встеча завтро в 10",
            "втреча завтра в 11",
            "созовон завтра в 12",
            "встреча 15 сентебря в 10",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_CREATE)
                self.assertIsNotNone(_parse_datetime(text, self.now))

    def test_ne_zabud_is_a_request_not_negation(self):
        text = "не забудь завтра в 10 позвонить маме"
        self.assertEqual(detect_intent(text).name, INTENT_CREATE)
        self.assertEqual(_extract_title(text), "Позвонить маме")

    def test_capability_questions_do_not_create_events(self):
        cases = [
            "можно ли поставить встречу завтра в 10?",
            "как поставить встречу завтра в 10?",
            "умеешь ли ты создать событие завтра в 10?",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_UNKNOWN)

    def test_polite_question_request_does_create(self):
        self.assertEqual(
            detect_intent("можешь поставить встречу завтра в 10?").name,
            INTENT_CREATE,
        )

    def test_invalid_clock_is_not_silently_truncated(self):
        for text in [
            "встреча завтра в 25:00",
            "встреча завтра в 9:75",
            "встреча завтра в 23:99",
        ]:
            with self.subTest(text=text):
                self.assertIsNone(_parse_datetime(text, self.now))

    def test_suspicious_backwards_day_range_is_rejected(self):
        self.assertIsNone(_parse_event_timing("встреча завтра с 18 до 16", self.now))
        self.assertEqual(
            _parse_event_timing("дежурство завтра с 23 до 1", self.now),
            (datetime(2026, 9, 13, 23, 0), datetime(2026, 9, 14, 1, 0)),
        )

    def test_punctuation_and_emoji_do_not_break_parser(self):
        self.assertEqual(
            _parse_datetime("Встреча с Иваном, завтра, в 15:00 👋", self.now),
            datetime(2026, 9, 13, 15, 0),
        )
        self.assertEqual(
            _parse_datetime("врач завтра 19:30 🩺", self.now),
            datetime(2026, 9, 13, 19, 30),
        )

    def test_task_verbs_without_time_ask_for_time(self):
        cases = [
            "сделать отчет завтра",
            "проверить договор завтра",
            "закончить презентацию в понедельник",
            "отправить документы во вторник",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_CREATE)
                self.assertTrue(_needs_time(text))

    def test_weekday_and_weekend_recurrence(self):
        self.assertEqual(
            _recurrence_rule("планерка по будням в 9"),
            "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        )
        self.assertEqual(
            _recurrence_rule("тренировка каждые выходные в 10"),
            "RRULE:FREQ=WEEKLY;BYDAY=SA,SU",
        )
        self.assertEqual(_recurrence_rule("отчет ежегодно 1 сентября"), "RRULE:FREQ=YEARLY")

    def test_birthday_with_word_time_is_not_all_day(self):
        self.assertFalse(is_all_day("день рождения Ивана завтра в девять"))

    def test_vacation_and_business_trip_without_time_are_all_day(self):
        self.assertTrue(is_all_day("отпуск завтра"))
        self.assertTrue(is_all_day("командировка на следующей неделе"))
        self.assertFalse(is_all_day("командировка завтра в 10"))

    def test_next_week_business_trip_builds_full_week(self):
        event = build_all_day_event(
            "командировка на следующей неделе",
            "Europe/Moscow",
            self.now,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["start"]["date"], "2026-09-14")
        self.assertEqual(event["end"]["date"], "2026-09-21")

    def test_same_month_all_day_range_is_inclusive_for_user(self):
        event = build_all_day_event(
            "отпуск с 15 по 20 сентября",
            "Europe/Moscow",
            self.now,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["start"]["date"], "2026-09-15")
        self.assertEqual(event["end"]["date"], "2026-09-21")

    def test_cross_month_all_day_range(self):
        event = build_all_day_event(
            "командировка с 28 сентября по 3 октября",
            "Europe/Moscow",
            self.now,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["start"]["date"], "2026-09-28")
        self.assertEqual(event["end"]["date"], "2026-10-04")

    def test_single_day_vacation(self):
        event = build_all_day_event("отпуск завтра", "Europe/Moscow", self.now)
        self.assertIsNotNone(event)
        self.assertEqual(event["start"]["date"], "2026-09-13")
        self.assertEqual(event["end"]["date"], "2026-09-14")


class UserTimezoneCreationTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_parser_uses_users_local_now(self):
        class User:
            id = 101

        class Message:
            def __init__(self):
                self.replies = []

            async def reply_text(self, text):
                self.replies.append(text)

        class Update:
            effective_user = User()
            message = Message()

        class Context:
            user_data = {}

        start = datetime(2026, 9, 13, 10, 0)
        end = start + timedelta(hours=1)
        local_now = datetime(2026, 9, 12, 23, 30, tzinfo=ZoneInfo("Asia/Vladivostok"))

        fake_datetime = MagicMock()
        fake_datetime.now.return_value = local_now

        with patch("modules.calendar_actions.get_user_timezone", return_value="Asia/Vladivostok"), \
             patch("modules.calendar_actions.get_category_colors", return_value={}), \
             patch("modules.calendar_actions.datetime", fake_datetime), \
             patch("modules.calendar_actions._parse_event_timing", return_value=(start, end)) as timing, \
             patch("modules.calendar_actions._find_conflicts", return_value=[]), \
             patch("modules.calendar_actions._create_event"):
            handled = await create_from_text(Update(), Context(), "встреча завтра в 10")

        self.assertTrue(handled)
        passed_now = timing.call_args.args[1]
        self.assertEqual(passed_now, datetime(2026, 9, 12, 23, 30))


if __name__ == "__main__":
    unittest.main()
