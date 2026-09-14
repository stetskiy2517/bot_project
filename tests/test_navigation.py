from datetime import datetime
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from integrations.navigation_2gis import _route_result as _dgis_route_result
from integrations.navigation_google import _route_result as _google_route_result
from integrations.navigation_ors import (
    _point_from_geocode,
    _profile_for_mode,
    _route_body as _ors_route_body,
    _route_result as _ors_route_result,
)
from modules.navigation import (
    RouteEstimate,
    _event_destination,
    _resolved_event_destination,
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

    def test_ors_geocoder_point_is_parsed(self):
        lon, lat = _point_from_geocode({
            "features": [{"geometry": {"coordinates": [37.6331, 55.8299]}}],
        })
        self.assertEqual(lon, 37.6331)
        self.assertEqual(lat, 55.8299)

    def test_ors_route_response_parses_duration_and_distance(self):
        seconds, meters = _ors_route_result({
            "routes": [{"summary": {"duration": 1900.4, "distance": 12800.2}}],
        })
        self.assertEqual(seconds, 1901)
        self.assertEqual(meters, 12801)

    def test_ors_route_body_uses_lon_lat_order(self):
        body = _ors_route_body((37.61, 55.75), (37.63, 55.82))
        self.assertEqual(body["coordinates"], [[37.61, 55.75], [37.63, 55.82]])
        self.assertFalse(body["geometry"])
        self.assertFalse(body["instructions"])
        self.assertEqual(body["units"], "m")

    def test_ors_profiles_support_driving_and_walking(self):
        self.assertEqual(_profile_for_mode("driving"), "driving-car")
        self.assertEqual(_profile_for_mode("walking"), "foot-walking")

    def test_ors_public_api_does_not_fake_transit_route(self):
        with self.assertRaisesRegex(ValueError, "public transport"):
            _profile_for_mode("transit")

    @patch("modules.navigation.ors_estimate", return_value=(27, 9100))
    @patch("modules.navigation.ors_configured", return_value=True)
    @patch("modules.navigation.navigation_provider", return_value="OpenRouteService")
    def test_estimate_route_uses_ors_provider(self, provider, configured, ors_estimate):
        departure = datetime(2099, 9, 14, 14, 10, tzinfo=self.zone)
        result = estimate_route("Дом", "Офис", mode="driving", departure_at=departure)
        self.assertEqual(result.duration_minutes, 27)
        self.assertEqual(result.distance_meters, 9100)
        ors_estimate.assert_called_once_with("Дом", "Офис", mode="driving", departure_at=departure)

    def test_google_route_response_remains_supported_as_fallback(self):
        seconds, meters = _google_route_result({
            "routes": [{"duration": "1900.4s", "distanceMeters": 12800}],
        })
        self.assertEqual(seconds, 1901)
        self.assertEqual(meters, 12800)

    def test_2gis_route_response_remains_supported_as_fallback(self):
        seconds, meters = _dgis_route_result({
            "status": "OK",
            "result": [{"total_duration": 1901, "total_distance": 12800}],
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
        self.assertEqual(private["smartPlannerRouteProvider"], "OpenRouteService")
        self.assertEqual(private["smartPlannerRouteMinutes"], "32")
        self.assertEqual(private["smartPlannerArrivalBufferMinutes"], "15")

    def test_natural_destination_is_recognized_from_summary(self):
        event = {"summary": "Встреча с клиентом будет на ВДНХ"}
        self.assertEqual(_event_destination(event), "ВДНХ")

    def test_short_prepositional_destination_is_recognized(self):
        self.assertEqual(_event_destination({"summary": "Прогулка на ВДНХ"}), "ВДНХ")
        self.assertEqual(
            _event_destination({"summary": "Прогулка на ВДНХ после рабочей встречи"}),
            "ВДНХ",
        )

    def test_temporal_phrases_are_not_treated_as_destinations(self):
        self.assertIsNone(_event_destination({"summary": "Встреча на завтра"}))
        self.assertIsNone(_event_destination({"summary": "Созвон в пятницу"}))
        self.assertIsNone(_event_destination({"summary": "Ужин в 21:00"}))

    def test_saved_home_and_office_aliases_resolve_to_addresses(self):
        prefs = {
            "home_address": "Москва, проспект Мира, 1",
            "office_address": "Москва, Гиляровского, 53",
        }
        self.assertEqual(
            _resolved_event_destination({"summary": "Ужин дома"}, prefs),
            "Москва, проспект Мира, 1",
        )
        self.assertEqual(
            _resolved_event_destination({"summary": "Встреча на работе"}, prefs),
            "Москва, Гиляровского, 53",
        )
        self.assertEqual(
            _resolved_event_destination({"location": "Дом"}, prefs),
            "Москва, проспект Мира, 1",
        )
        self.assertEqual(
            _resolved_event_destination({"location": "Офис"}, prefs),
            "Москва, Гиляровского, 53",
        )

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

    @patch("modules.navigation.get_category_colors", return_value={"travel": "7"})
    @patch("modules.navigation._get_calendar_service")
    @patch("modules.navigation.estimate_route")
    @patch("modules.navigation._event_end")
    @patch("modules.navigation._list_events")
    @patch("modules.navigation.navigation_configured", return_value=True)
    @patch("modules.navigation.get_navigation_preferences")
    def test_dinner_at_home_then_meeting_at_work_creates_home_to_office_travel(
        self,
        prefs,
        configured,
        list_events,
        event_end,
        estimate,
        get_service,
        colors,
    ):
        home = "Москва, проспект Мира, 1"
        office = "Москва, Гиляровского, 53"
        prefs.return_value = {
            "enabled": True,
            "home_address": home,
            "office_address": office,
            "default_origin": home,
            "mode": "driving",
            "arrival_buffer_minutes": 15,
        }
        dinner = {
            "id": "dinner-19",
            "summary": "Ужин дома",
            "start": {"dateTime": "2026-09-14T19:00:00+03:00", "timeZone": "Europe/Moscow"},
            "end": {"dateTime": "2026-09-14T20:00:00+03:00", "timeZone": "Europe/Moscow"},
        }
        meeting = {
            "id": "meeting-21",
            "summary": "Встреча на работе",
            "start": {"dateTime": "2026-09-14T21:00:00+03:00", "timeZone": "Europe/Moscow"},
            "end": {"dateTime": "2026-09-14T22:00:00+03:00", "timeZone": "Europe/Moscow"},
        }
        list_events.return_value = [dinner]
        event_end.return_value = datetime(2026, 9, 14, 20, 0, tzinfo=self.zone)
        estimate.return_value = RouteEstimate(
            origin=home,
            destination=office,
            mode="driving",
            duration_minutes=35,
            distance_meters=12000,
        )
        service = MagicMock()
        service.events().insert().execute.return_value = {"id": "travel-home-office"}
        get_service.return_value = service

        result = create_travel_for_event(1, meeting, "Europe/Moscow")

        self.assertEqual(result["id"], "travel-home-office")
        estimate.assert_called_once_with(
            home,
            office,
            mode="driving",
            departure_at=datetime(2026, 9, 14, 21, 0, tzinfo=self.zone),
        )
        inserted = service.events().insert.call_args.kwargs["body"]
        self.assertEqual(inserted["location"], office)
        self.assertEqual(inserted["end"]["dateTime"], "2026-09-14T21:00:00+03:00")

    @patch("modules.navigation.get_category_colors", return_value={"travel": "7"})
    @patch("modules.navigation._get_calendar_service")
    @patch("modules.navigation.estimate_route")
    @patch("modules.navigation._event_end")
    @patch("modules.navigation._list_events")
    @patch("modules.navigation.navigation_configured", return_value=True)
    @patch("modules.navigation.get_navigation_preferences")
    def test_work_meeting_then_vdnh_walk_creates_office_to_vdnh_travel(
        self,
        prefs,
        configured,
        list_events,
        event_end,
        estimate,
        get_service,
        colors,
    ):
        home = "Москва, проспект Мира, 1"
        office = "Москва, Гиляровского, 53"
        prefs.return_value = {
            "enabled": True,
            "home_address": home,
            "office_address": office,
            "default_origin": home,
            "mode": "driving",
            "arrival_buffer_minutes": 15,
        }
        meeting = {
            "id": "meeting-21",
            "summary": "Встреча на работе",
            "start": {"dateTime": "2026-09-14T21:00:00+03:00", "timeZone": "Europe/Moscow"},
            "end": {"dateTime": "2026-09-14T22:00:00+03:00", "timeZone": "Europe/Moscow"},
        }
        walk = {
            "id": "walk-vdnh",
            "summary": "Прогулка на ВДНХ",
            "start": {"dateTime": "2026-09-14T23:00:00+03:00", "timeZone": "Europe/Moscow"},
            "end": {"dateTime": "2026-09-15T00:00:00+03:00", "timeZone": "Europe/Moscow"},
        }
        list_events.return_value = [meeting]
        event_end.return_value = datetime(2026, 9, 14, 22, 0, tzinfo=self.zone)
        estimate.return_value = RouteEstimate(
            origin=office,
            destination="ВДНХ",
            mode="driving",
            duration_minutes=20,
            distance_meters=7600,
        )
        service = MagicMock()
        service.events().insert().execute.return_value = {"id": "travel-office-vdnh"}
        get_service.return_value = service

        result = create_travel_for_event(1, walk, "Europe/Moscow")

        self.assertEqual(result["id"], "travel-office-vdnh")
        estimate.assert_called_once_with(
            office,
            "ВДНХ",
            mode="driving",
            departure_at=datetime(2026, 9, 14, 23, 0, tzinfo=self.zone),
        )
        inserted = service.events().insert.call_args.kwargs["body"]
        self.assertEqual(inserted["location"], "ВДНХ")
        self.assertEqual(inserted["end"]["dateTime"], "2026-09-14T23:00:00+03:00")

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
