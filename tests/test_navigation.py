from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from modules.navigation import (
    RouteEstimate,
    _event_destination,
    _matrix_duration_seconds,
    build_travel_event,
    create_travel_for_event,
    resolve_origin,
)


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("Europe/Moscow")
        self.source = {
            "id": "meeting-123",
            "summary": "Встреча с клиентом",
            "location": "ВДНХ",
            "start": {"dateTime": "2026-09-14T15:00:00+03:00", "timeZone": "Europe/Moscow"},
            "end": {"dateTime": "2026-09-14T16:00:00+03:00", "timeZone": "Europe/Moscow"},
        }

    def test_matrix_response_parses_duration_and_distance(self):
        seconds, meters = _matrix_duration_seconds({
            "rows": [{"elements": [{
                "status": "OK",
                "duration": {"value": 1901},
                "distance": {"value": 12800},
            }]}]
        })
        self.assertEqual(seconds, 1901)
        self.assertEqual(meters, 12800)

    def test_travel_block_includes_route_and_arrival_buffer(self):
        estimate = RouteEstimate(
            origin="Москва, Гиляровского, 53",
            destination="ВДНХ",
            mode="driving",
            duration_minutes=32,
            distance_meters=12000,
        )
        travel = build_travel_event(
            self.source,
            estimate,
            timezone="Europe/Moscow",
            arrival_buffer_minutes=15,
            color_id="7",
        )
        self.assertEqual(travel["start"]["dateTime"], "2026-09-14T14:13:00+03:00")
        self.assertEqual(travel["end"]["dateTime"], "2026-09-14T15:00:00+03:00")
        self.assertEqual(travel["location"], "ВДНХ")
        private = travel["extendedProperties"]["private"]
        self.assertEqual(private["smartPlannerSourceEventId"], "meeting-123")
        self.assertEqual(private["smartPlannerRouteMinutes"], "32")
        self.assertEqual(private["smartPlannerArrivalBufferMinutes"], "15")

    def test_natural_destination_is_recognized_from_summary(self):
        event = {"summary": "Встреча с клиентом будет на ВДНХ"}
        self.assertEqual(_event_destination(event), "ВДНХ")

    @patch("modules.navigation.get_navigation_preferences")
    @patch("modules.navigation._event_end")
    @patch("modules.navigation._list_events")
    def test_previous_calendar_location_has_priority_over_default_origin(self, list_events, event_end, prefs):
        previous = {
            "id": "office-block",
            "summary": "Работа в офисе",
            "location": "Москва, Гиляровского, 53",
            "end": {"dateTime": "2026-09-14T14:00:00+03:00"},
        }
        list_events.return_value = [previous]
        event_end.return_value = datetime(2026, 9, 14, 14, 0, tzinfo=self.zone)
        prefs.return_value = {"default_origin": "Дом"}
        origin = resolve_origin(1, self.source, "Europe/Moscow")
        self.assertEqual(origin, "Москва, Гиляровского, 53")

    @patch("modules.navigation.navigation_configured", return_value=True)
    @patch("modules.navigation.get_navigation_preferences")
    @patch("modules.navigation.estimate_route")
    def test_recurring_source_does_not_create_single_incorrect_travel_block(self, estimate, prefs, configured):
        prefs.return_value = {"enabled": True, "mode": "driving", "arrival_buffer_minutes": 15}
        event = dict(self.source)
        event["recurrence"] = ["RRULE:FREQ=WEEKLY"]
        result = create_travel_for_event(1, event, "Europe/Moscow")
        self.assertIsNone(result)
        estimate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
