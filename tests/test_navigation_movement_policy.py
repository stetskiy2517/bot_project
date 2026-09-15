from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from modules import navigation, navigation_recurring


class NavigationMovementPolicyTests(unittest.TestCase):
    def setUp(self):
        self.prefs = {
            "enabled": True,
            "home_address": "Москва, проспект Мира, 1",
            "office_address": "Москва, Гиляровского, 53",
            "mode": "driving",
            "arrival_buffer_minutes": 15,
        }

    def test_commute_does_not_get_an_extra_transfer(self):
        event = {
            "id": "commute",
            "summary": "Дорога на работу",
            "location": "Офис",
            "start": {"dateTime": "2099-09-16T09:00:00+03:00"},
            "end": {"dateTime": "2099-09-16T10:00:00+03:00"},
        }
        with patch("modules.navigation.get_navigation_preferences", return_value=self.prefs), \
             patch("modules.navigation.navigation_configured", return_value=True), \
             patch("modules.navigation.remove_navigation_origin_request") as remove_origin, \
             patch("modules.navigation.remove_navigation_optimization_request") as remove_optimization, \
             patch("modules.navigation.queue_navigation_origin_request") as queue_origin, \
             patch("modules.navigation.estimate_route") as estimate:
            result = navigation.create_travel_for_event(1, event, "Europe/Moscow")

        self.assertIsNone(result)
        self.assertEqual(event["_smartPlannerNavigation"]["status"], "movement_event")
        remove_origin.assert_called_once_with(1, "commute")
        remove_optimization.assert_called_once_with(1, "commute")
        queue_origin.assert_not_called()
        estimate.assert_not_called()

    def test_explicit_transfer_to_airport_is_already_a_movement(self):
        event = {
            "id": "airport-transfer",
            "summary": "Трансфер в аэропорт Шереметьево",
            "location": "Москва, аэропорт Шереметьево",
            "start": {"dateTime": "2099-09-16T12:00:00+03:00"},
            "end": {"dateTime": "2099-09-16T13:00:00+03:00"},
        }
        self.assertTrue(navigation._is_movement_source_event(event))
        self.assertIsNone(navigation._movement_access_destination(event, self.prefs))

    def test_flight_gets_access_transfer_to_departure_airport(self):
        event = {
            "id": "flight",
            "summary": "DP6865 Москва — Саратов",
            "description": "Рейс DP6865",
            "location": "Москва, аэропорт Шереметьево D",
            "extendedProperties": {"private": {
                "smartPlannerMovement": "1",
                "smartPlannerStartLocation": "Москва, аэропорт Шереметьево D",
                "smartPlannerEndLocation": "Саратов, аэропорт Гагарин",
            }},
        }
        self.assertTrue(navigation._is_movement_source_event(event))
        self.assertEqual(
            navigation._movement_access_destination(event, self.prefs),
            "Москва, аэропорт Шереметьево D",
        )
        self.assertEqual(
            navigation._event_end_location(1, event, self.prefs),
            "Саратов, аэропорт Гагарин",
        )

    def test_train_gets_access_transfer_to_departure_station(self):
        event = {
            "id": "train",
            "summary": "Поезд Москва — Саратов",
            "location": "Москва, Павелецкий вокзал",
            "extendedProperties": {"private": {
                "smartPlannerMovement": "1",
                "smartPlannerStartLocation": "Москва, Павелецкий вокзал",
                "smartPlannerEndLocation": "Саратов, железнодорожный вокзал",
            }},
        }
        self.assertEqual(
            navigation._movement_access_destination(event, self.prefs),
            "Москва, Павелецкий вокзал",
        )

    def test_generic_imported_movement_does_not_get_access_transfer(self):
        event = {
            "id": "taxi",
            "summary": "Такси домой",
            "location": "Офис",
            "extendedProperties": {"private": {
                "smartPlannerMovement": "1",
                "smartPlannerStartLocation": "Офис",
                "smartPlannerEndLocation": "Дом",
            }},
        }
        self.assertTrue(navigation._is_movement_source_event(event))
        self.assertIsNone(navigation._movement_access_destination(event, self.prefs))

    def test_recurring_commute_is_skipped_by_background_worker(self):
        zone = ZoneInfo("Europe/Moscow")
        event = {
            "id": "commute-instance",
            "recurringEventId": "commute-series",
            "summary": "Дорога на работу",
            "location": "Офис",
        }
        now = datetime(2099, 9, 16, 8, 0, tzinfo=timezone.utc)
        start = datetime(2099, 9, 16, 12, 0, tzinfo=zone)
        with patch("modules.navigation_recurring.navigation_configured", return_value=True), \
             patch("modules.navigation_recurring.get_navigation_preferences", return_value=self.prefs), \
             patch("modules.navigation_recurring.get_user_timezone", return_value="Europe/Moscow"), \
             patch("modules.navigation_recurring._list_events", return_value=[event]), \
             patch("modules.navigation_recurring._linked_travel_events", return_value=[]), \
             patch("modules.navigation_recurring._event_start", return_value=(start, False)), \
             patch("modules.navigation_recurring._previous_event_context") as previous, \
             patch("modules.navigation_recurring.estimate_route") as estimate:
            created = navigation_recurring.sync_recurring_travel_for_user(1, now=now)

        self.assertEqual(created, 0)
        previous.assert_not_called()
        estimate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
