from datetime import datetime
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from modules import navigation, navigation_extra_api, navigation_monitor
from modules.navigation import PreviousEventContext, RouteEstimate


class NavigationContextRuleTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("Europe/Moscow")
        self.target = {
            "id": "target",
            "summary": "Ужин дома",
            "location": "Дом",
            "start": {"dateTime": "2099-09-14T15:00:00+03:00", "timeZone": "Europe/Moscow"},
            "end": {"dateTime": "2099-09-14T16:00:00+03:00", "timeZone": "Europe/Moscow"},
        }
        self.prefs = {
            "enabled": True,
            "home_address": "Москва, проспект Мира, 1",
            "office_address": "Москва, Гиляровского, 53",
            "default_origin": "Москва, проспект Мира, 1",
            "mode": "driving",
            "arrival_buffer_minutes": 15,
        }

    @patch("modules.navigation._event_end")
    @patch("modules.navigation._list_events")
    def test_previous_event_is_used_only_within_one_hour(self, list_events, event_end):
        near = {"id": "near", "summary": "Работа", "location": "Офис"}
        old = {"id": "old", "summary": "Обед", "location": "Дом"}
        list_events.return_value = [old, near]
        event_end.side_effect = lambda event, _tz: {
            "old": datetime(2099, 9, 14, 13, 0, tzinfo=self.zone),
            "near": datetime(2099, 9, 14, 14, 30, tzinfo=self.zone),
        }[event["id"]]

        context = navigation._previous_event_context(1, self.target, "Europe/Moscow", self.prefs)

        self.assertIsNotNone(context)
        self.assertEqual(context.event["id"], "near")
        self.assertEqual(context.end_location, self.prefs["office_address"])
        query_start = list_events.call_args.args[1]
        self.assertEqual(query_start, datetime(2099, 9, 14, 14, 0, tzinfo=self.zone))

    @patch("modules.navigation._event_end")
    @patch("modules.navigation._list_events")
    def test_event_more_than_one_hour_ago_is_not_an_origin(self, list_events, event_end):
        old = {"id": "old", "summary": "Работа", "location": "Офис"}
        list_events.return_value = [old]
        event_end.return_value = datetime(2099, 9, 14, 13, 59, tzinfo=self.zone)

        context = navigation._previous_event_context(1, self.target, "Europe/Moscow", self.prefs)

        self.assertIsNone(context)

    def test_movement_event_uses_its_end_location_not_start_location(self):
        flight = {
            "id": "flight",
            "summary": "Перелёт",
            "location": "Шереметьево",
            "extendedProperties": {"private": {
                "smartPlannerMovement": "1",
                "smartPlannerStartLocation": "Шереметьево",
                "smartPlannerEndLocation": "Саратов",
            }},
        }
        self.assertEqual(
            navigation._event_end_location(1, flight, self.prefs),
            "Саратов",
        )

    def test_travel_block_is_clamped_to_previous_event_end(self):
        estimate = RouteEstimate(
            origin=self.prefs["office_address"],
            destination=self.prefs["home_address"],
            mode="driving",
            duration_minutes=40,
            distance_meters=12000,
        )
        travel = navigation.build_travel_event(
            self.target,
            estimate,
            timezone="Europe/Moscow",
            arrival_buffer_minutes=15,
            color_id="7",
            earliest_start=datetime(2099, 9, 14, 14, 30, tzinfo=self.zone),
            origin_source="previous_event",
        )

        self.assertEqual(travel["start"]["dateTime"], "2099-09-14T14:30:00+03:00")
        private = travel["extendedProperties"]["private"]
        self.assertEqual(private["smartPlannerRouteConflict"], "1")
        self.assertEqual(private["smartPlannerMissingMinutes"], "25")

    @patch("modules.navigation.estimate_route")
    @patch("modules.navigation.queue_navigation_origin_request")
    @patch("modules.navigation._previous_event_context", return_value=None)
    @patch("modules.navigation.navigation_configured", return_value=True)
    @patch("modules.navigation.get_navigation_preferences")
    def test_missing_previous_event_asks_for_origin_instead_of_using_default(
        self, prefs, _configured, _previous, queue, estimate
    ):
        prefs.return_value = self.prefs
        target = dict(self.target)
        target["location"] = "ВДНХ"

        result = navigation.create_travel_for_event(1, target, "Europe/Moscow")

        self.assertIsNone(result)
        self.assertEqual(target["_smartPlannerNavigation"]["status"], "origin_required")
        queue.assert_called_once()
        estimate.assert_not_called()

    @patch("modules.navigation.remove_navigation_optimization_request")
    @patch("modules.navigation.infer_event_end_location")
    @patch("modules.navigation._event_end")
    @patch("modules.navigation._list_events")
    @patch("modules.navigation.estimate_route")
    @patch("modules.navigation.queue_navigation_origin_request")
    @patch("modules.navigation.remove_navigation_origin_request")
    @patch("modules.navigation.navigation_configured", return_value=True)
    @patch("modules.navigation.get_navigation_preferences")
    def test_same_place_after_ambiguous_previous_event_creates_no_transfer(
        self, prefs, _configured, _remove_origin, queue, estimate, list_events, event_end, infer, _remove_optimization
    ):
        prefs.return_value = self.prefs
        walk = {
            "id": "walk",
            "summary": "Прогулка с собакой",
            "start": {"dateTime": "2099-09-14T14:00:00+03:00"},
            "end": {"dateTime": "2099-09-14T14:45:00+03:00"},
        }
        list_events.return_value = [walk]
        event_end.return_value = datetime(2099, 9, 14, 14, 45, tzinfo=self.zone)
        infer.return_value = self.prefs["home_address"]

        result = navigation.create_travel_for_event(1, self.target, "Europe/Moscow")

        self.assertIsNone(result)
        self.assertEqual(self.target["_smartPlannerNavigation"]["status"], "same_location")
        estimate.assert_not_called()
        queue.assert_not_called()

    @patch("modules.navigation._get_calendar_service")
    @patch("modules.navigation.queue_navigation_optimization_request")
    @patch("modules.navigation.estimate_route")
    @patch("modules.navigation._previous_event_context")
    @patch("modules.navigation.remove_navigation_origin_request")
    @patch("modules.navigation.navigation_configured", return_value=True)
    @patch("modules.navigation.get_navigation_preferences")
    def test_insufficient_gap_offers_optimization_instead_of_partial_transfer(
        self, prefs, _configured, _remove, previous, estimate, queue_optimization, get_service
    ):
        prefs.return_value = self.prefs
        target = dict(self.target)
        target["location"] = "ВДНХ"
        previous.return_value = PreviousEventContext(
            event={"id": "office", "summary": "Работа в офисе"},
            end=datetime(2099, 9, 14, 14, 30, tzinfo=self.zone),
            end_location=self.prefs["office_address"],
        )
        estimate.return_value = RouteEstimate(
            origin=self.prefs["office_address"],
            destination="ВДНХ",
            mode="driving",
            duration_minutes=40,
            distance_meters=15000,
        )
        service = MagicMock()
        get_service.return_value = service

        result = navigation.create_travel_for_event(1, target, "Europe/Moscow")

        self.assertIsNone(result)
        service.events().insert.assert_not_called()
        self.assertEqual(target["_smartPlannerNavigation"]["status"], "optimization_required")
        self.assertEqual(target["_smartPlannerNavigation"]["missing_minutes"], 25)
        queue_optimization.assert_called_once()
        kwargs = queue_optimization.call_args.kwargs
        self.assertEqual(kwargs["required_minutes"], 55)
        self.assertEqual(kwargs["available_minutes"], 30)
        self.assertEqual(kwargs["missing_minutes"], 25)

    @patch("modules.navigation._get_calendar_service")
    @patch("modules.navigation.queue_navigation_optimization_request")
    @patch("modules.navigation.estimate_route")
    @patch("modules.navigation._previous_event_context")
    @patch("modules.navigation.remove_navigation_origin_request")
    @patch("modules.navigation.navigation_configured", return_value=True)
    @patch("modules.navigation.get_navigation_preferences")
    def test_zero_gap_never_creates_zero_length_transfer(
        self, prefs, _configured, _remove, previous, estimate, queue_optimization, get_service
    ):
        prefs.return_value = self.prefs
        target = dict(self.target)
        target["location"] = "ВДНХ"
        previous.return_value = PreviousEventContext(
            event={"id": "office", "summary": "Работа в офисе"},
            end=datetime(2099, 9, 14, 15, 0, tzinfo=self.zone),
            end_location=self.prefs["office_address"],
        )
        estimate.return_value = RouteEstimate(
            origin=self.prefs["office_address"],
            destination="ВДНХ",
            mode="driving",
            duration_minutes=30,
            distance_meters=12000,
        )
        service = MagicMock()
        get_service.return_value = service

        result = navigation.create_travel_for_event(1, target, "Europe/Moscow")

        self.assertIsNone(result)
        service.events().insert.assert_not_called()
        self.assertEqual(target["_smartPlannerNavigation"]["status"], "optimization_required")
        self.assertEqual(target["_smartPlannerNavigation"]["missing_minutes"], 45)
        self.assertEqual(queue_optimization.call_args.kwargs["available_minutes"], 0)

    @patch("modules.navigation.remove_navigation_optimization_request")
    @patch("modules.navigation._get_calendar_service")
    @patch("modules.navigation.get_category_colors", return_value={"travel": "7"})
    @patch("modules.navigation.estimate_route")
    @patch("modules.navigation._previous_event_context", return_value=None)
    @patch("modules.navigation.remove_navigation_origin_request")
    @patch("modules.navigation.navigation_configured", return_value=True)
    @patch("modules.navigation.get_navigation_preferences")
    def test_user_selected_origin_can_create_route_without_previous_event(
        self, prefs, _configured, _remove_origin, _previous, estimate, _colors, get_service, _remove_optimization
    ):
        prefs.return_value = self.prefs
        target = dict(self.target)
        target["location"] = "ВДНХ"
        estimate.return_value = RouteEstimate(
            origin=self.prefs["home_address"],
            destination="ВДНХ",
            mode="driving",
            duration_minutes=20,
            distance_meters=8000,
        )
        service = MagicMock()
        service.events().insert().execute.return_value = {"id": "travel-2"}
        get_service.return_value = service

        result = navigation.create_travel_for_event(
            1,
            target,
            "Europe/Moscow",
            origin_override=self.prefs["home_address"],
        )

        self.assertEqual(result["id"], "travel-2")
        self.assertEqual(target["_smartPlannerNavigation"]["status"], "created")

    @patch("modules.navigation_extra_api._interval_conflict", return_value=None)
    def test_optimization_snapshot_builds_shorten_and_shift_options(self, _conflict):
        previous = {
            "id": "previous",
            "summary": "Встреча",
            "start": {"dateTime": "2099-09-14T14:00:00+03:00"},
            "end": {"dateTime": "2099-09-14T15:00:00+03:00"},
        }
        target = {
            "id": "target",
            "summary": "Следующая встреча",
            "start": {"dateTime": "2099-09-14T15:00:00+03:00"},
            "end": {"dateTime": "2099-09-14T16:00:00+03:00"},
        }
        events = {"previous": previous, "target": target}
        service = MagicMock()
        service.events().get.side_effect = lambda **kwargs: MagicMock(execute=lambda: events[kwargs["eventId"]])
        item = {
            "event_id": "target",
            "previous_event_id": "previous",
            "timezone": "Europe/Moscow",
            "origin": "Офис",
            "destination": "ВДНХ",
            "required_minutes": 45,
            "available_minutes": 0,
            "missing_minutes": 45,
        }

        snapshot = navigation_extra_api._optimization_snapshot(1, item, service)

        self.assertEqual(snapshot["missing_minutes"], 45)
        self.assertEqual(snapshot["shorten_end"].isoformat(), "2099-09-14T14:15:00+03:00")
        self.assertTrue(snapshot["can_shorten"])
        self.assertEqual(snapshot["shifted_start"].isoformat(), "2099-09-14T15:45:00+03:00")
        self.assertEqual(snapshot["shifted_end"].isoformat(), "2099-09-14T16:45:00+03:00")


class NavigationMonitorWindowTests(unittest.TestCase):
    def test_monitor_patch_never_moves_route_before_previous_event_end(self):
        zone = ZoneInfo("Europe/Moscow")
        source_start = datetime(2099, 9, 14, 16, 0, tzinfo=zone)
        estimate = RouteEstimate(
            origin="Офис",
            destination="ВДНХ",
            mode="driving",
            duration_minutes=50,
            distance_meters=15000,
        )
        travel = {
            "id": "travel",
            "extendedProperties": {"private": {
                "smartPlannerRouteMinutes": "20",
                "smartPlannerArrivalBufferMinutes": "15",
            }},
        }
        service = MagicMock()
        service.events().patch().execute.return_value = {}

        navigation_monitor._patch_travel_event(
            service,
            travel,
            source_start,
            estimate,
            15,
            "Europe/Moscow",
            datetime(2099, 9, 14, 15, 20, tzinfo=zone),
            False,
            travel["extendedProperties"]["private"],
            datetime(2099, 9, 14, 15, 30, tzinfo=zone),
            "previous_event",
            mark_leave_alert=False,
            mark_early_alert=False,
        )

        body = service.events().patch.call_args.kwargs["body"]
        self.assertEqual(body["start"]["dateTime"], "2099-09-14T15:30:00+03:00")
        self.assertEqual(body["extendedProperties"]["private"]["smartPlannerRouteConflict"], "1")
        self.assertEqual(body["extendedProperties"]["private"]["smartPlannerMissingMinutes"], "35")


if __name__ == "__main__":
    unittest.main()
