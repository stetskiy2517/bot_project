from datetime import datetime, timedelta
import unittest
from unittest.mock import AsyncMock, patch

from modules.calendar import (
    _detect_category,
    _extract_duration,
    _extract_title,
    _parse_datetime,
    _parse_event_timing,
)
from modules.calendar_event_features import (
    _recurrence_rule,
    _reminder_minutes,
    apply_event_features,
    build_all_day_event,
)
from modules.router import (
    INTENT_CREATE,
    INTENT_UNKNOWN,
    _needs_time,
    _resume_pending,
    detect_intent,
)


class HardUserParserBetaTests(unittest.TestCase):
    def setUp(self):
        # Saturday, 12 September 2026, 17:00
        self.now = datetime(2026, 9, 12, 17, 0)

    def assert_dt(self, text, expected):
        self.assertEqual(_parse_datetime(text, self.now), expected, text)

    def test_everyday_create_intents(self):
        cases = [
            "встреча с Иваном завтра в 15",
            "забрать Влада завтра в 18",
            "купить продукты завтра в 19",
            "оплатить садик 15 сентября в 10",
            "напомни через 2 часа позвонить клиенту",
            "мне завтра в 9 к врачу",
            "давай завтра в 10 созвон с Петей",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_CREATE)

    def test_natural_dated_tasks_without_time_request_clarification(self):
        cases = [
            "забрать Влада завтра",
            "купить продукты завтра",
            "оплатить садик 15 сентября",
            "заехать в офис в понедельник",
            "позвонить маме во вторник",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_CREATE)
                self.assertTrue(_needs_time(text))

    def test_questions_and_negations_do_not_create(self):
        cases = [
            "у меня встреча завтра в 10?",
            "встреча завтра в 10?",
            "не создавай встречу завтра в 10",
            "не добавляй врача завтра в 19",
            "не ставь созвон в пятницу в 12",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertNotEqual(detect_intent(text).name, INTENT_CREATE)

    def test_common_date_typos(self):
        self.assert_dt("встреча сегодя в 19", datetime(2026, 9, 12, 19, 0))
        self.assert_dt("встреча завтро в 10", datetime(2026, 9, 13, 10, 0))
        self.assert_dt("встреча послезавтро в 10", datetime(2026, 9, 14, 10, 0))
        self.assert_dt("врач в понеделник в 9", datetime(2026, 9, 14, 9, 0))
        self.assert_dt("врач в пятнцу в 19", datetime(2026, 9, 18, 19, 0))
        self.assert_dt("кино в суботу в 20", datetime(2026, 9, 12, 20, 0))

    def test_spoken_and_human_time_forms(self):
        cases = {
            "встреча завтра в девять": datetime(2026, 9, 13, 9, 0),
            "встреча завтра в девятнадцать": datetime(2026, 9, 13, 19, 0),
            "ужин завтра в семь вечера": datetime(2026, 9, 13, 19, 0),
            "ужин завтра вечером в семь": datetime(2026, 9, 13, 19, 0),
            "врач завтра утром в девять": datetime(2026, 9, 13, 9, 0),
            "встреча завтра в 19 часов 30 минут": datetime(2026, 9, 13, 19, 30),
            "встреча завтра в 19-30": datetime(2026, 9, 13, 19, 30),
            "встреча завтра в половине восьмого вечера": datetime(2026, 9, 13, 19, 30),
            "встреча завтра пол восьмого вечера": datetime(2026, 9, 13, 19, 30),
            "кино завтра без четверти девять вечера": datetime(2026, 9, 13, 20, 45),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assert_dt(text, expected)

    def test_relative_time_human_forms(self):
        cases = {
            "напомни через 90 мин позвонить": datetime(2026, 9, 12, 18, 30),
            "напомни через 2 ч позвонить": datetime(2026, 9, 12, 19, 0),
            "напомни через два часа позвонить": datetime(2026, 9, 12, 19, 0),
            "напомни через пару часов позвонить": datetime(2026, 9, 12, 19, 0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assert_dt(text, expected)

    def test_invalid_explicit_dates_are_not_silently_reinterpreted(self):
        for text in [
            "встреча 31.02 в 10",
            "встреча 31-04 в 10",
            "встреча 29.02.2025 в 10",
        ]:
            with self.subTest(text=text):
                self.assertIsNone(_parse_datetime(text, self.now))

    def test_time_ranges_human_forms(self):
        cases = {
            "встреча завтра с 14 до 16": (datetime(2026, 9, 13, 14, 0), datetime(2026, 9, 13, 16, 0)),
            "встреча завтра с 14-30 до 16-15": (datetime(2026, 9, 13, 14, 30), datetime(2026, 9, 13, 16, 15)),
            "встреча завтра 14:00-16:00": (datetime(2026, 9, 13, 14, 0), datetime(2026, 9, 13, 16, 0)),
            "дежурство завтра с 23 до 1": (datetime(2026, 9, 13, 23, 0), datetime(2026, 9, 14, 1, 0)),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_parse_event_timing(text, self.now), expected)

    def test_duration_shorthand_and_decimal(self):
        cases = {
            "встреча на 45 мин": timedelta(minutes=45),
            "созвон на 2ч": timedelta(hours=2),
            "врач на 1.5 часа": timedelta(minutes=90),
            "тренировка на 1,5 часа": timedelta(minutes=90),
            "встреча на полчаса": timedelta(minutes=30),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_extract_duration(text), expected)

    def test_title_cleanup_for_live_requests(self):
        cases = {
            "напомни завтра в 19 позвонить маме": "Позвонить маме",
            "пожалуйста запланируй завтра в 10 встречу с Иваном": "Встречу с Иваном",
            "можешь поставить завтра в 11 созвон с Петей": "Созвон с Петей",
            "давай завтра в 12 обед с Олегом": "Обед с Олегом",
            "запланируй плиз врача завтра в 9": "Врача",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_extract_title(text), expected)

    def test_category_matrix_for_live_phrases(self):
        cases = {
            "забрать сына из школы": "family",
            "ужин с родителями": "family",
            "созвон с клиентом": "work",
            "записаться к стоматологу": "health",
            "сходить в кино": "rest",
            "отпуск на море": "rest",
            "рейс в Питер": "travel",
            "купить продукты": "personal",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_detect_category(text)[0], expected)

    def test_recurrence_live_phrases(self):
        cases = {
            "созвон каждый понедельник в 10": "RRULE:FREQ=WEEKLY;BYDAY=MO",
            "созвон по понедельникам в 10": "RRULE:FREQ=WEEKLY;BYDAY=MO",
            "отчет раз в неделю в пятницу": "RRULE:FREQ=WEEKLY",
            "тренировка каждые 2 недели в субботу": "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=SA",
            "таблетки каждый день в 9": "RRULE:FREQ=DAILY",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_recurrence_rule(text), expected)

    def test_reminder_shorthand(self):
        self.assertEqual(_reminder_minutes("напомни за 15 мин"), [15])
        self.assertEqual(_reminder_minutes("напомни за 2 ч"), [120])
        self.assertEqual(_reminder_minutes("напомни за сутки"), [1440])

    def test_all_day_family_birthday(self):
        event = build_all_day_event(
            "день рождения дочери завтра",
            "Europe/Moscow",
            self.now,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["start"]["date"], "2026-09-13")
        self.assertEqual(event["description"], "AI Smart Planner category: family")

    def test_feature_cleanup_keeps_clean_summary(self):
        event = {
            "summary": "raw",
            "start": {"dateTime": "2026-09-13T10:00:00+03:00", "timeZone": "Europe/Moscow"},
            "end": {"dateTime": "2026-09-13T11:00:00+03:00", "timeZone": "Europe/Moscow"},
        }
        enriched = apply_event_features(
            event,
            "пожалуйста создай встречу завтра в 10 напомни за 15 мин пригласи a@example.com",
        )
        self.assertEqual(enriched["summary"], "Встречу")
        self.assertEqual(enriched["reminders"]["overrides"][0]["minutes"], 15)
        self.assertEqual(enriched["attendees"], [{"email": "a@example.com"}])


class PendingTimeBetaTests(unittest.IsolatedAsyncioTestCase):
    async def test_bare_hour_reply_is_accepted(self):
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
        with patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as create:
            handled = await _resume_pending(update, context, "19")
        self.assertTrue(handled)
        create.assert_awaited_once()
        combined = create.await_args.args[2]
        self.assertIn("в 19", combined)

    async def test_evening_word_reply_is_accepted(self):
        class Context:
            def __init__(self):
                self.user_data = {"smart_planner_pending": {"type": "create_time", "text": "ужин завтра"}}

        class Message:
            async def reply_text(self, text):
                pass

        class Update:
            message = Message()

        context = Context()
        with patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as create:
            handled = await _resume_pending(Update(), context, "7 вечера")
        self.assertTrue(handled)
        create.assert_awaited_once()
        combined = create.await_args.args[2]
        self.assertIn("в 7 вечера", combined)


if __name__ == "__main__":
    unittest.main()
