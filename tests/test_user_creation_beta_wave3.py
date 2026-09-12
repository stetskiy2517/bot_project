from datetime import datetime, timedelta
import unittest

from modules.calendar import _extract_duration, _extract_title, _parse_datetime
from modules.calendar_event_features import _reminder_minutes, apply_event_features, build_all_day_event, is_all_day
from modules.router import INTENT_CREATE, INTENT_UNKNOWN, detect_intent


class HardUserParserWave3Tests(unittest.TestCase):
    def test_explicit_today_past_time_is_not_created_in_the_past(self):
        now = datetime(2026, 9, 12, 17, 0)
        self.assertIsNone(_parse_datetime("встреча сегодня в 10", now))
        self.assertEqual(_parse_datetime("встреча сегодня в 19", now), datetime(2026, 9, 12, 19, 0))

    def test_year_rollover_for_named_and_numeric_dates(self):
        now = datetime(2026, 12, 30, 12, 0)
        self.assertEqual(_parse_datetime("встреча 5 января в 10", now), datetime(2027, 1, 5, 10, 0))
        self.assertEqual(_parse_datetime("встреча 05.01 в 10", now), datetime(2027, 1, 5, 10, 0))

    def test_valid_leap_day(self):
        now = datetime(2026, 9, 12, 17, 0)
        self.assertEqual(_parse_datetime("встреча 29.02.2028 в 10", now), datetime(2028, 2, 29, 10, 0))

    def test_month_typo_is_parsed_to_the_intended_date(self):
        now = datetime(2026, 9, 12, 17, 0)
        self.assertEqual(_parse_datetime("встреча 15 сентебря в 10", now), datetime(2026, 9, 15, 10, 0))

    def test_midnight_and_noon_forms(self):
        now = datetime(2026, 9, 12, 17, 0)
        cases = {
            "рейс завтра в 00:00": datetime(2026, 9, 13, 0, 0),
            "рейс завтра в полночь": datetime(2026, 9, 13, 0, 0),
            "обед завтра в полдень": datetime(2026, 9, 13, 12, 0),
            "сон завтра в 12 ночи": datetime(2026, 9, 13, 0, 0),
            "обед завтра в 12 дня": datetime(2026, 9, 13, 12, 0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_parse_datetime(text, now), expected)

    def test_compact_hhmm_after_preposition(self):
        now = datetime(2026, 9, 12, 17, 0)
        self.assertEqual(_parse_datetime("встреча завтра в 930", now), datetime(2026, 9, 13, 9, 30))
        self.assertEqual(_parse_datetime("созвон завтра в 1930", now), datetime(2026, 9, 13, 19, 30))

    def test_composite_relative_duration(self):
        now = datetime(2026, 9, 12, 17, 0)
        self.assertEqual(
            _parse_datetime("напомни через 1 час 30 минут позвонить", now),
            datetime(2026, 9, 12, 18, 30),
        )
        self.assertEqual(
            _parse_datetime("напомни через 2 часа 15 минут позвонить", now),
            datetime(2026, 9, 12, 19, 15),
        )

    def test_composite_event_duration(self):
        self.assertEqual(_extract_duration("встреча на 1 час 30 минут"), timedelta(minutes=90))
        self.assertEqual(_extract_duration("встреча на 2 часа 15 минут"), timedelta(minutes=135))

    def test_weather_statement_is_not_calendar_event(self):
        cases = [
            "завтра в 10 будет дождь",
            "завтра в 8 температура 5 градусов",
            "завтра в 12 прогноз погоды",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_UNKNOWN)

    def test_terse_real_event_is_still_created(self):
        cases = [
            "Иванов 15 сентября в 14",
            "Защита проекта завтра в 11",
            "созвон завтра в 10",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_CREATE)

    def test_single_day_and_cross_year_all_day_ranges(self):
        now = datetime(2026, 9, 12, 17, 0)
        same = build_all_day_event("отпуск с 15 по 15 сентября", "Europe/Moscow", now)
        self.assertEqual(same["start"]["date"], "2026-09-15")
        self.assertEqual(same["end"]["date"], "2026-09-16")
        cross_year = build_all_day_event("отпуск с 29 декабря по 3 января", "Europe/Moscow", now)
        self.assertEqual(cross_year["start"]["date"], "2026-12-29")
        self.assertEqual(cross_year["end"]["date"], "2027-01-04")

    def test_dash_all_day_range(self):
        now = datetime(2026, 9, 12, 17, 0)
        event = build_all_day_event("отпуск 15-20 сентября", "Europe/Moscow", now)
        self.assertIsNotNone(event)
        self.assertEqual(event["start"]["date"], "2026-09-15")
        self.assertEqual(event["end"]["date"], "2026-09-21")

    def test_all_day_titles_are_clean(self):
        now = datetime(2026, 9, 12, 17, 0)
        trip = build_all_day_event("командировка на следующей неделе", "Europe/Moscow", now)
        vacation = build_all_day_event("отпуск с 15 по 20 сентября", "Europe/Moscow", now)
        self.assertEqual(trip["summary"], "Командировка")
        self.assertEqual(vacation["summary"], "Отпуск")

    def test_birthday_noon_and_midnight_are_timed(self):
        self.assertFalse(is_all_day("день рождения Ивана завтра в полдень"))
        self.assertFalse(is_all_day("день рождения Ивана завтра в полночь"))

    def test_reminders_with_singular_day_and_week(self):
        self.assertEqual(_reminder_minutes("напомни за 1 день"), [1440])
        self.assertEqual(_reminder_minutes("напомни за неделю"), [10080])
        self.assertEqual(_reminder_minutes("напомни за 2 недели"), [20160])
        self.assertEqual(_reminder_minutes("напомни за 1 день и за 15 минут"), [1440, 15])

    def test_recurrence_and_reminder_cleanup_in_summary(self):
        event = {
            "summary": "raw",
            "start": {"dateTime": "2026-09-14T09:00:00+03:00", "timeZone": "Europe/Moscow"},
            "end": {"dateTime": "2026-09-14T10:00:00+03:00", "timeZone": "Europe/Moscow"},
        }
        enriched = apply_event_features(event, "планерка по будням в 9 напомни за 15 минут")
        self.assertEqual(enriched["summary"], "Планерка")
        self.assertEqual(enriched["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"])
        self.assertEqual(enriched["reminders"]["overrides"][0]["minutes"], 15)

    def test_title_cleanup_with_word_clock(self):
        self.assertEqual(_extract_title("встреча завтра в двадцать один тридцать"), "Встреча")


if __name__ == "__main__":
    unittest.main()
