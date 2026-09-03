from datetime import datetime, timedelta
import unittest

from modules.calendar import (
    _build_event,
    _detect_category,
    _extract_duration,
    _extract_title,
    _parse_datetime,
    _parse_event_timing,
)


class CalendarParserTests(unittest.TestCase):
    def setUp(self):
        # Wednesday, 2 September 2026, 15:30
        self.now = datetime(2026, 9, 2, 15, 30)

    def assert_parsed(self, text, expected):
        self.assertEqual(_parse_datetime(text, self.now), expected)

    def test_relative_dates(self):
        self.assert_parsed("встреча сегодня в 19:00", datetime(2026, 9, 2, 19, 0))
        self.assert_parsed("встреча завтра в 10 30", datetime(2026, 9, 3, 10, 30))
        self.assert_parsed("встреча послезавтра в 9", datetime(2026, 9, 4, 9, 0))
        self.assert_parsed("напомни через 30 минут позвонить", datetime(2026, 9, 2, 16, 0))
        self.assert_parsed("созвон через 2 часа", datetime(2026, 9, 2, 17, 30))
        self.assert_parsed("встреча через неделю в 10", datetime(2026, 9, 9, 10, 0))

    def test_weekdays(self):
        self.assert_parsed("невролог в пятницу в 19 00", datetime(2026, 9, 4, 19, 0))
        self.assert_parsed("созвон в среду в 14:00", datetime(2026, 9, 9, 14, 0))
        self.assert_parsed("созвон в следующий понедельник в 10", datetime(2026, 9, 7, 10, 0))

    def test_explicit_dates(self):
        self.assert_parsed("встреча 05.09 в 12:15", datetime(2026, 9, 5, 12, 15))
        self.assert_parsed("встреча 5 сентября в 12:15", datetime(2026, 9, 5, 12, 15))
        self.assert_parsed("встреча 05-09-2026 в 19.30", datetime(2026, 9, 5, 19, 30))

    def test_time_without_date(self):
        self.assert_parsed("созвон в 19", datetime(2026, 9, 2, 19, 0))
        self.assert_parsed("созвон в 10", datetime(2026, 9, 3, 10, 0))

    def test_natural_time(self):
        self.assert_parsed("врач завтра в полдень", datetime(2026, 9, 3, 12, 0))
        self.assert_parsed("вылет завтра в полночь", datetime(2026, 9, 3, 0, 0))
        self.assert_parsed("невролог в пятницу в половине восьмого", datetime(2026, 9, 4, 7, 30))
        self.assert_parsed("звонок завтра без четверти девять", datetime(2026, 9, 3, 8, 45))
        self.assert_parsed("встреча завтра в 7 вечера", datetime(2026, 9, 3, 19, 0))

    def test_requires_time(self):
        self.assertIsNone(_parse_datetime("встреча завтра", self.now))
        self.assertIsNone(_parse_datetime("просто текст", self.now))

    def test_duration(self):
        self.assertEqual(_extract_duration("встреча на 2 часа"), timedelta(hours=2))
        self.assertEqual(_extract_duration("врач на 30 минут"), timedelta(minutes=30))
        self.assertEqual(_extract_duration("созвон на полтора часа"), timedelta(minutes=90))
        self.assertEqual(_extract_duration("обычная встреча"), timedelta(hours=1))

    def test_time_range(self):
        timing = _parse_event_timing("встреча завтра с 14 до 16", self.now)
        self.assertEqual(timing, (datetime(2026, 9, 3, 14, 0), datetime(2026, 9, 3, 16, 0)))
        timing = _parse_event_timing("созвон завтра с 14:30 до 16:15", self.now)
        self.assertEqual(timing, (datetime(2026, 9, 3, 14, 30), datetime(2026, 9, 3, 16, 15)))

    def test_categories_and_colors(self):
        self.assertEqual(_detect_category("поход к неврологу"), ("health", "6"))
        self.assertEqual(_detect_category("рабочая встреча с клиентом"), ("work", "3"))
        self.assertEqual(_detect_category("вечером кино и отдых"), ("rest", "10"))
        self.assertEqual(_detect_category("рейс в Москву"), ("travel", "7"))
        self.assertEqual(_detect_category("непонятное событие"), ("other", None))

    def test_event_contains_color_and_duration(self):
        start = datetime(2026, 9, 4, 19, 0)
        event = _build_event("поход к неврологу в пятницу в 19 на 2 часа", start)
        self.assertEqual(event["colorId"], "6")
        self.assertEqual(event["end"]["dateTime"], datetime(2026, 9, 4, 21, 0).isoformat())

    def test_title_cleanup(self):
        self.assertEqual(_extract_title("поставь поход к неврологу в пятницу в 19 00"), "Поход к неврологу")
        self.assertEqual(_extract_title("создай встречу завтра в 10:30"), "Встречу")
        self.assertEqual(_extract_title("звонок 5 сентября в 12:15"), "Звонок")
        self.assertEqual(_extract_title("назначь невролога в пятницу в половине восьмого на час"), "Невролога")


if __name__ == "__main__":
    unittest.main()
