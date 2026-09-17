from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from modules.language_support import canonicalize_english, detect_input_language
from modules.router import (
    INTENT_CREATE,
    INTENT_DELETE,
    INTENT_FREE,
    INTENT_UPDATE,
    INTENT_VIEW,
    detect_intent,
)
from modules.calendar import _detect_category
from modules.reminder_categories import detect_reminder_category


class EnglishLanguageNormalizationTests(unittest.TestCase):
    def test_language_detection(self):
        self.assertEqual(detect_input_language("Meeting tomorrow at 3"), "en")
        self.assertEqual(detect_input_language("Встреча завтра в 15"), "ru")
        self.assertEqual(detect_input_language("Meeting с Иваном tomorrow"), "en")

    def test_create_and_time_are_normalized_without_translating_title(self):
        self.assertEqual(
            canonicalize_english("Schedule a meeting with John tomorrow at 3 pm"),
            "запланируй meeting with John завтра в 15:00",
        )
        self.assertEqual(
            canonicalize_english("Doctor appointment tomorrow at ten am"),
            "Doctor appointment завтра в 10:00",
        )

    def test_queries_updates_and_free_time(self):
        self.assertEqual(canonicalize_english("What's on Friday?"), "что у меня пятница?")
        self.assertEqual(canonicalize_english("When am I free tomorrow?"), "когда я свободен завтра?")
        self.assertEqual(
            canonicalize_english("Move the meeting with John to Friday at 4 pm"),
            "перенеси meeting with John на пятница в 16:00",
        )
        self.assertEqual(
            canonicalize_english("Rename the meeting with John to Client review"),
            "переименуй meeting with John в Client review",
        )

    def test_reminders_tasks_notes_and_ranges(self):
        self.assertEqual(
            canonicalize_english("Remind me to call Alice in 30 minutes"),
            "напомни call Alice через 30 минут",
        )
        self.assertEqual(
            canonicalize_english("Create a task send the report tomorrow high priority"),
            "создай задачу send the report завтра высокий приоритет",
        )
        self.assertEqual(
            canonicalize_english("Create a note: Ideas for Q4"),
            "создай заметку: Ideas for Q4",
        )
        self.assertEqual(
            canonicalize_english("Vacation from September 20 to September 27"),
            "отпуск с 20 сентября по 27 сентября",
        )

    def test_property_updates(self):
        self.assertEqual(
            canonicalize_english("Set the category of meeting with John to work"),
            "измени категорию у meeting with John на работа",
        )
        self.assertEqual(
            canonicalize_english("Set meeting with John priority to high"),
            "измени приоритет у meeting with John на высокий приоритет",
        )


class EnglishIntentTests(unittest.TestCase):
    def test_calendar_intents(self):
        cases = {
            "Schedule a meeting with John tomorrow at 3 pm": INTENT_CREATE,
            "Meeting with John tomorrow at 3 pm": INTENT_CREATE,
            "What's on Friday?": INTENT_VIEW,
            "When am I free tomorrow?": INTENT_FREE,
            "Move the meeting with John to Friday at 4 pm": INTENT_UPDATE,
            "Cancel the meeting with John tomorrow": INTENT_DELETE,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, expected)


class EnglishCategoryTests(unittest.TestCase):
    def test_event_categories(self):
        cases = {
            "Meeting with a client": "work",
            "Doctor appointment": "health",
            "Gym workout": "rest",
            "Flight to London": "travel",
            "Dinner with my daughter": "family",
            "Haircut": "personal",
            "Business trip": "travel",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_detect_category(text)[0], expected)

    def test_reminder_categories_use_same_english_rules(self):
        self.assertEqual(detect_reminder_category("Call client about contract"), "work")
        self.assertEqual(detect_reminder_category("Take medicine"), "health")
        self.assertEqual(detect_reminder_category("Pick up my daughter"), "family")


class EnglishSpeechConfigurationTests(unittest.TestCase):
    def test_assemblyai_is_limited_to_ru_and_en_auto_detection(self):
        from integrations import speech

        response = MagicMock()
        response.json.return_value = {"id": "transcript-1"}
        with patch.object(speech._impl.requests, "post", return_value=response) as post:
            transcript_id = speech._start_transcription("https://example.invalid/audio")

        self.assertEqual(transcript_id, "transcript-1")
        payload = post.call_args.kwargs["json"]
        self.assertTrue(payload["language_detection"])
        self.assertEqual(payload["language_detection_options"]["expected_languages"], ["ru", "en"])
        self.assertEqual(payload["language_detection_options"]["fallback_language"], "auto")
        self.assertEqual(payload["speech_models"], ["universal-3-pro", "universal-2"])


if __name__ == "__main__":
    unittest.main()
