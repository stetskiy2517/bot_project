from datetime import datetime
import unittest
from unittest.mock import AsyncMock, patch

from core.location_context import current_location_origin, save_current_location
from integrations import navigation_2gis, navigation_ors
from modules.calendar import _extract_title, _parse_event_timing
from modules.navigation import _summary_destination, resolve_origin
from modules.router import INTENT_CREATE, INTENT_UNKNOWN, _creation_text, _needs_time, _resume_pending, detect_intent


class NaturalCalendarCommandTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 14, 12, 0)

    def test_common_activity_phrases_with_time_are_create_commands(self):
        cases = [
            "прогулка в ботанический сад 22:00",
            "прогулка ботанический сад 22:00",
            "прогулка на ВДНХ сегодня в 22",
            "погулять по ВДНХ завтра в 19",
            "тренировка Лужники 20:30",
            "театр завтра в 19:00",
            "массаж в 18:30",
            "бассейн завтра в 7",
            "футбол в субботу в 16",
            "ужин дома 19:00",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_CREATE)

    def test_bare_activity_without_time_prompts(self):
        for text in ["прогулка ботанический сад", "прогулка на ВДНХ", "погулять по ВДНХ", "тренировка Лужники", "театр Большой", "массаж"]:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_CREATE)
                self.assertTrue(_needs_time(_creation_text(text)))

    def test_trailing_bare_hour_is_repaired_without_date(self):
        self.assertEqual(_creation_text("прогулка ботанический сад 22"), "прогулка ботанический сад в 22")
        self.assertEqual(_creation_text("тренировка Лужники 20"), "тренировка Лужники в 20")

    def test_statements_questions_weather_and_news_are_not_events(self):
        cases = [
            "прогулки полезны для здоровья",
            "я был на прогулке вчера",
            "тренировка сегодня была тяжелой",
            "как пройти в ботанический сад",
            "где прогулка завтра?",
            "погода в ботаническом саду завтра в 22:00",
            "новости ВДНХ завтра в 22:00",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text).name, INTENT_UNKNOWN)

    def test_parser_keeps_place_and_time(self):
        timing = _parse_event_timing("прогулка в ботанический сад 22:00", self.now)
        self.assertIsNotNone(timing)
        self.assertEqual(timing[0], datetime(2026, 9, 14, 22, 0))
        self.assertEqual(_extract_title("прогулка в ботанический сад 22:00"), "Прогулка в ботанический сад")


class NavigationDestinationTests(unittest.TestCase):
    def test_natural_destinations(self):
        cases = {
            "Прогулка в ботанический сад": "ботанический сад",
            "Прогулка на ВДНХ": "ВДНХ",
            "Прогулка ботанический сад": "ботанический сад",
            "Погулять ботанический сад": "ботанический сад",
            "Прогуляться по ВДНХ": "ВДНХ",
            "Тренировка Лужники": "Лужники",
            "Поездка Шереметьево": "Шереметьево",
        }
        for summary, expected in cases.items():
            with self.subTest(summary=summary):
                self.assertEqual(_summary_destination(summary), expected)

    def test_non_locations_are_rejected(self):
        for summary in ["Прогулка с детьми", "Прогулка после работы", "Прогулка перед сном", "Тренировка с тренером", "Прогулка на завтра", "Прогулка в пятницу", "Прогулка в 21:00"]:
            with self.subTest(summary=summary):
                self.assertIsNone(_summary_destination(summary))

    def test_live_location_is_first_origin(self):
        save_current_location(901, 55.7812, 37.6331, 25)
        with patch("modules.navigation._previous_event_origin", return_value="Москва, Тверская, 1"):
            self.assertEqual(resolve_origin(901, {"start": {"dateTime": "2026-09-14T22:00:00+03:00"}}, "Europe/Moscow", {"default_origin": "Дом"}), current_location_origin(901))

    def test_coordinate_origin_bypasses_geocoder(self):
        self.assertEqual(navigation_2gis._coordinate_origin("geo:55.7812,37.6331"), (55.7812, 37.6331))
        self.assertEqual(navigation_ors._coordinate_origin("geo:55.7812,37.6331"), (37.6331, 55.7812))


class PendingNaturalEventTimeTests(unittest.IsolatedAsyncioTestCase):
    class Context:
        def __init__(self):
            self.user_data = {"smart_planner_pending": {"type": "create_time", "text": "прогулка ботанический сад"}}
    class Message:
        def __init__(self): self.replies = []
        async def reply_text(self, text): self.replies.append(text)
    class User: id = 1
    class Update:
        def __init__(self):
            self.message = PendingNaturalEventTimeTests.Message()
            self.effective_user = PendingNaturalEventTimeTests.User()

    async def test_pending_accepts_tomorrow_plus_bare_hour(self):
        context = self.Context()
        update = self.Update()
        with patch("modules.router.create_from_text", new=AsyncMock(return_value=True)) as create:
            handled = await _resume_pending(update, context, "завтра 22")
        self.assertTrue(handled)
        combined = create.await_args.args[2]
        self.assertEqual(combined, "прогулка ботанический сад завтра в 22")


if __name__ == "__main__":
    unittest.main()
