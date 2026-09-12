from datetime import datetime, timedelta
import unittest
from unittest.mock import AsyncMock, patch

from modules.calendar import _extract_duration, _extract_time, _parse_datetime
from modules.calendar_event_features import _reminder_minutes, build_all_day_event
from modules.router import INTENT_CREATE, INTENT_UNKNOWN, _needs_time, _resume_pending, detect_intent


class HardUserParserWave4Tests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 12, 17, 0)

    def test_compact_time_does_not_confuse_year(self):
        self.assertEqual(_extract_time("встреча завтра в 930"), (9, 30))
        self.assertEqual(_extract_time("встреча завтра в 1930"), (19, 30))
        self.assertIsNone(_extract_time("отпуск в 2026 году"))
        self.assertIsNone(_parse_datetime("встреча завтра в 2960", self.now))

    def test_exact_current_time_is_not_created_in_past(self):
        self.assertIsNone(_parse_datetime("встреча сегодня в 17", self.now))
        self.assertEqual(_parse_datetime("встреча сегодня в 17:01", self.now), datetime(2026, 9, 12, 17, 1))

    def test_shorthand_composite_offsets(self):
        self.assertEqual(
            _parse_datetime("напомни через 2 ч 15 мин позвонить", self.now),
            datetime(2026, 9, 12, 19, 15),
        )
        self.assertEqual(_extract_duration("встреча на 2 ч 15 мин"), timedelta(minutes=135))

    def test_relative_day_alias(self):
        self.assertEqual(
            _parse_datetime("напомни через сутки оплатить счет", self.now),
            datetime(2026, 9, 13, 17, 0),
        )

    def test_weekday_abbreviations(self):
        cases = {
            "созвон в пн в 10": datetime(2026, 9, 14, 10, 0),
            "врач в пт в 19": datetime(2026, 9, 18, 19, 0),
            "кино в сб в 20": datetime(2026, 9, 12, 20, 0),
            "обед в вс в 13": datetime(2026, 9, 13, 13, 0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_parse_datetime(text, self.now), expected)
                self.assertEqual(detect_intent(text).name, INTENT_CREATE)

    def test_daypart_without_preposition(self):
        self.assertEqual(_parse_datetime("встреча завтра 9 утра", self.now), datetime(2026, 9, 13, 9, 0))
        self.assertEqual(_parse_datetime("ужин завтра 9 вечера", self.now), datetime(2026, 9, 13, 21, 0))

    def test_ordinal_named_date(self):
        self.assertEqual(
            _parse_datetime("встреча 15-го сентября в 10", self.now),
            datetime(2026, 9, 15, 10, 0),
        )

    def test_no_time_daypart_still_asks_for_time(self):
        text = "встреча завтра вечером"
        self.assertEqual(detect_intent(text).name, INTENT_CREATE)
        self.assertTrue(_needs_time(text))

    def test_stronger_negations_do_not_create(self):
        cases = [
            "не надо создавать встречу завтра в 10",
            "не надо добавлять врача завтра в 19",
            "только не ставь созвон завтра в 12",
            "пока не создавай встречу завтра в 15",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_UNKNOWN)

    def test_explicit_weather_request_is_allowed(self):
        self.assertEqual(
            detect_intent("добавь прогноз погоды завтра в 12").name,
            INTENT_CREATE,
        )

    def test_train_and_flight_terse_phrases(self):
        cases = [
            "поезд Москва Питер завтра в 19:30",
            "рейс SU100 завтра в 8",
            "такси в аэропорт завтра в 6:30",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_CREATE)
                self.assertIsNotNone(_parse_datetime(text, self.now))

    def test_week_reminders_mix_correctly(self):
        self.assertEqual(_reminder_minutes("напомни за неделю и за день"), [10080, 1440])
        self.assertEqual(_reminder_minutes("напомни за 2 недели и за 30 минут"), [20160, 30])

    def test_dash_variants_for_all_day_ranges(self):
        for dash in ["-", "–", "—"]:
            with self.subTest(dash=dash):
                event = build_all_day_event(f"отпуск 15{dash}20 сентября", "Europe/Moscow", self.now)
                self.assertIsNotNone(event)
                self.assertEqual(event["start"]["date"], "2026-09-15")
                self.assertEqual(event["end"]["date"], "2026-09-21")
                self.assertEqual(event["summary"], "Отпуск")

    def test_plain_non_calendar_statements_stay_unknown(self):
        cases = [
            "завтра в 10 курс доллара 90",
            "завтра в 11 температура воздуха 12 градусов",
            "завтра в 9 новости",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_UNKNOWN)


class PendingFinalBetaTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_can_be_cancelled_with_ne_nado(self):
        class Context:
            def __init__(self):
                self.user_data = {"smart_planner_pending": {"type": "create_time", "text": "встреча завтра"}}

        class Message:
            def __init__(self):
                self.replies = []

            async def reply_text(self, text):
                self.replies.append(text)

        class Update:
            def __init__(self):
                self.message = Message()

        context = Context()
        update = Update()
        with patch("modules.router.create_from_text", new=AsyncMock()) as create:
            handled = await _resume_pending(update, context, "не надо")
        self.assertTrue(handled)
        create.assert_not_awaited()
        self.assertNotIn("smart_planner_pending", context.user_data)

    async def test_pending_accepts_compact_1930(self):
        class Context:
            def __init__(self):
                self.user_data = {"smart_planner_pending": {"type": "create_time", "text": "созвон завтра"}}

        class Message:
            async def reply_text(self, text):
                pass

        class Update:
            message = Message()

        context = Context()
        with patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as create:
            handled = await _resume_pending(Update(), context, "1930")
        self.assertTrue(handled)
        create.assert_awaited_once()
        self.assertIn("в 1930", create.await_args.args[2])


if __name__ == "__main__":
    unittest.main()
