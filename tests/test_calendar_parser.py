from datetime import datetime
import unittest

from modules.calendar import _extract_title, _parse_datetime


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

    def test_weekdays(self):
        self.assert_parsed("невролог в пятницу в 19 00", datetime(2026, 9, 4, 19, 0))
        self.assert_parsed("созвон в среду в 14:00", datetime(2026, 9, 9, 14, 0))

    def test_explicit_dates(self):
        self.assert_parsed("встреча 05.09 в 12:15", datetime(2026, 9, 5, 12, 15))
        self.assert_parsed("встреча 5 сентября в 12:15", datetime(2026, 9, 5, 12, 15))

    def test_time_without_date(self):
        self.assert_parsed("созвон в 19", datetime(2026, 9, 2, 19, 0))
        self.assert_parsed("созвон в 10", datetime(2026, 9, 3, 10, 0))

    def test_requires_time(self):
        self.assertIsNone(_parse_datetime("встреча завтра", self.now))
        self.assertIsNone(_parse_datetime("просто текст", self.now))

    def test_title_cleanup(self):
        self.assertEqual(_extract_title("поставь поход к неврологу в пятницу в 19 00"), "Поход к неврологу")
        self.assertEqual(_extract_title("создай встречу завтра в 10:30"), "Встречу")
        self.assertEqual(_extract_title("звонок 5 сентября в 12:15"), "Звонок")


if __name__ == "__main__":
    unittest.main()
