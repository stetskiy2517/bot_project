import unittest

from modules.reminders import _repeat_rule, _repeat_suffix


class ReminderRecurrenceIndicatorTests(unittest.TestCase):
    def test_plain_2300_reminder_is_one_off_and_shows_no_repeat(self):
        text = "Напомни в 23:00 выпить таблетку"
        self.assertIsNone(_repeat_rule(text))
        self.assertEqual(_repeat_suffix({"repeat_rule": None}), " · повтор: нет")

    def test_daily_reminder_shows_daily_repeat(self):
        text = "Каждый день в 23:00 напомни выпить таблетку"
        self.assertEqual(_repeat_rule(text), "daily")
        self.assertEqual(
            _repeat_suffix({"repeat_rule": "daily"}),
            " · повтор: каждый день",
        )


if __name__ == "__main__":
    unittest.main()
