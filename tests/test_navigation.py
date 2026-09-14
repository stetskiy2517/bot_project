from datetime import datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from integrations.navigation_2gis import _point_from_item, _route_result as _dgis_route_result, _routing_body
from integrations.navigation_google import _route_body as _google_route_body, _route_result as _google_route_result
from modules.navigation import (
    RouteEstimate,
    _event_destination,
    build_travel_event,
    create_travel_for_event,
    estimate_route,
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

    def test_google_route_response_parses_duration_and_distance(self):
        seconds, meters = _google_route_result({
            "routes": [{"duration": "1900.4s", "distanceMeters": 12800}],
        })
        self.assertEqual(seconds, 1901)
        self.assertEqual(meters, 12800)

    def test_google_driving_route_uses_traffic_and_future_departure(self):
        departure = datetime(2099, 9, 14, 14, 10, tzinfo=self.zone)
        body = _google_route_body(
            "Москва, Гиляровского, 53",
            "ВДНХ, Москва",
            "driving",
            departure,
        )
        self.assertEqual(body["origin"], {"address": "Москва, Гиляровского, 53"})
        self.assertEqual(body["destination"], {"address": "ВДНХ, Москва"})
        self.assertEqual(body["travelMode"], "DRIVE")
        self.assertEqual(body["routingPreference"], "TRAFFIC_AWARE")
        self.assertTrue(body["departureTime"].endswith("Z"))
        self.assertEqual(body["regionCode"], "ru")

    def test_google_walking_route_does_not_request_traffic(self):
        departure = datetime(2099, 9, 14, 14, 10, tzinfo=self.zone)
        body = _google_route_body("Дом", "Офис", "walking", departure)
        self.assertEqual(body["travelMode"], "WALK")
        self.assertNotIn("routingPreference", body)
        self.assertNotIn("departureTime", body)

    def test_google_transit_route_uses_departure_time(self):
        departure = datetime.now(self.zone).replace(microsecond=0)
        body = _google_route_body("Дом", "Офис", "transit", departure)
        self.assertEqual(body["travelMode"], "TRANSIT")
        self.assertIn("departureTime", body)
        self.assertNotIn("routingPreference", body)

    @patch("modules.navigation.google_estimate", return_value=(27, 9100))
    @patch("modules.navigation.google_configured", return_value=True)
    @patch("modules.navigation.navigation_provider", return_value="google")
    def test_estimate_route_uses_google_provider(self, provider, configured, google_estimate):
        departure = datetime(2099, 9, 14, 14, 10, tzinfo=self.zone)
        result = estimate_route("Дом", "Офис", mode="driving", departure_at=departure)
        self.assertEqual(result.duration_minutes, 27)
        self.assertEqual(result.distance_meters, 9100)
        google_estimate.assert_called_once_with("Дом", "Офис", mode="driving", departure_at=departure)

    def test_2gis_route_response_remains_supported_as_fallback(self):
        seconds, meters = _dgis_route_result({
            "status": "OK",
            "result": [{"total_duration": 1901, "total_distance": 12800}],
        })
        self.assertEqual(seconds, 1901)
        self.assertEqual(meters, 12800)

    def test_2gis_geocoder_point_is_parsed(self):
        lat, lon = _point_from_item({"point": {"lat": 55.8299, "lon": 37.6331}})
        self.assertEqual(lat, 55.8299)
        self.assertEqual(lon, 37.6331)

    def test_2gis_future_driving_route_uses_statistical_traffic(self):
        departure = datetime(2099, 9, 14, 14, 10, tzinfo=self.zone)
        body = _routing_body((55.75, 37.61), (55.82, 37.63), "driving", departure)
        self.assertEqual(body["traffic_mode"], "statistics")
        self.assertEqual(body["utc"], int(departure.timestamp()))

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
        self.assertEqual(private["smartPlannerRouteProvider"], "google")
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
