import unittest

from modules.settings import _parse_work_days, _valid_hhmm


class CalendarSettingsTests(unittest.TestCase):
    def test_work_hours_format(self):
        self.assertTrue(_valid_hhmm("09:00"))
        self.assertTrue(_valid_hhmm("18:30"))
        self.assertFalse(_valid_hhmm("25:00"))

    def test_work_days_range(self):
        self.assertEqual(_parse_work_days(["1-5"]), [0, 1, 2, 3, 4])

    def test_work_days_words(self):
        self.assertEqual(_parse_work_days(["пн", "ср", "пт"]), [0, 2, 4])
        self.assertIsNone(_parse_work_days(["непонятно"]))


if __name__ == "__main__":
    unittest.main()
