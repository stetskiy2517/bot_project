from datetime import datetime
import unittest

from modules.calendar import _parse_datetime
from modules.reminders import REMINDER_CREATE, detect_reminder_intent, _reminder_title
from modules.router import (
    INTENT_SEARCH,
    INTENT_VIEW,
    _normalise_pending_reply,
    detect_intent,
)


class ShortNaturalCalendarPhrasesTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 13, 11, 15)

    def test_short_view_questions_are_understood(self):
        cases = [
            "что завтра в 14?",
            "есть что-нибудь завтра в 14?",
            "какие планы завтра?",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_VIEW)

    def test_natural_search_phrases_are_understood(self):
        cases = [
            "во сколько встреча с Иваном?",
            "найди мне встречу с Иваном",
            "во сколько завтра врач?",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_SEARCH)

    def test_short_bare_hour_after_date_is_time(self):
        cases = {
            "встреча завтра 14": datetime(2026, 9, 14, 14, 0),
            "врач 15 сентября 19": datetime(2026, 9, 15, 19, 0),
            "созвон послезавтра 9": datetime(2026, 9, 15, 9, 0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_parse_datetime(text, self.now), expected)


class ReminderVoiceOrderTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 13, 11, 15)

    def test_time_first_reminder_phrases_stay_standalone_reminders(self):
        cases = {
            "в 22:00 напомни выпить таблетку": "Выпить таблетку",
            "завтра в 9 напомни позвонить маме": "Позвонить маме",
            "через 20 минут напомни проверить духовку": "Проверить духовку",
        }
        for text, expected_title in cases.items():
            with self.subTest(text=text):
                self.assertEqual(detect_reminder_intent(text), REMINDER_CREATE)
                self.assertEqual(_reminder_title(text), expected_title)
                self.assertIsNotNone(_parse_datetime(text, self.now))

    def test_calendar_event_reminder_property_is_never_stolen(self):
        cases = [
            "добавь напоминание к встрече завтра",
            "поставь напоминание для события с Иваном",
            "создай встречу завтра в 10 напомни за 15 минут",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(detect_reminder_intent(text))


class NaturalPendingReplyTests(unittest.TestCase):
    def test_voice_style_yes_no_replies_are_reduced_to_control_words(self):
        cases = {
            "Да, конечно.": "да",
            "Да конечно": "да",
            "Конечно.": "да",
            "Нет, спасибо.": "нет",
            "Не надо, спасибо": "не надо",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_normalise_pending_reply(text).lower(), expected)


if __name__ == "__main__":
    unittest.main()
