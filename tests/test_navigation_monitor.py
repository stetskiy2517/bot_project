from datetime import datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.location_context import save_current_location
from modules.navigation import RouteEstimate
from modules import navigation_monitor


class _Result:
    def __init__(self, value=None, exc=None):
        self.value = value
        self.exc = exc

    def execute(self):
        if self.exc:
            raise self.exc
        return self.value


class _Events:
    def __init__(self, travel_event, source_event):
        self.travel_event = travel_event
        self.source_event = source_event
        self.patch_calls = []
        self.deleted = []

    def list(self, **_kwargs):
        return _Result({"items": [self.travel_event]})

    def get(self, **_kwargs):
        return _Result(self.source_event)

    def patch(self, **kwargs):
        self.patch_calls.append(kwargs)
        updated = dict(self.travel_event)
        updated.update(kwargs["body"])
        return _Result(updated)

    def delete(self, **kwargs):
        self.deleted.append(kwargs["eventId"])
        return _Result({})


class _Service:
    def __init__(self, events):
        self._events = events

    def events(self):
        return self._events


class NavigationMonitorTests(unittest.TestCase):
    def setUp(self):
        with navigation_monitor._last_recalc_lock:
            navigation_monitor._last_recalc.clear()

    def test_live_origin_rejects_very_inaccurate_position(self):
        save_current_location(8101, 55.75, 37.61, 1200)
        self.assertIsNone(navigation_monitor._fresh_live_origin(8101))
        save_current_location(8102, 55.75, 37.61, 25)
        self.assertEqual(navigation_monitor._fresh_live_origin(8102), "geo:55.7500000,37.6100000")

    def test_google_driving_is_marked_traffic_aware(self):
        zone = ZoneInfo("Europe/Moscow")
        now = datetime(2026, 9, 14, 15, 0, tzinfo=zone)
        with patch("modules.navigation_monitor.navigation_provider", return_value="google"):
            self.assertTrue(navigation_monitor._traffic_aware("driving", now, now=now))
            self.assertFalse(navigation_monitor._traffic_aware("walking", now, now=now))
        with patch("modules.navigation_monitor.navigation_provider", return_value="OpenRouteService"):
            self.assertFalse(navigation_monitor._traffic_aware("driving", now, now=now))

    def test_recalculation_moves_departure_earlier_and_pushes(self):
        zone = ZoneInfo("Europe/Moscow")
        now = datetime(2026, 9, 14, 15, 0, tzinfo=zone)
        source = {
            "id": "source-1",
            "summary": "Встреча на ВДНХ",
            "location": "ВДНХ",
            "start": {"dateTime": "2026-09-14T16:00:00+03:00"},
            "end": {"dateTime": "2026-09-14T17:00:00+03:00"},
        }
        travel = {
            "id": "travel-1",
            "summary": "Дорога -> ВДНХ",
            "start": {"dateTime": "2026-09-14T15:30:00+03:00"},
            "end": {"dateTime": "2026-09-14T16:00:00+03:00"},
            "extendedProperties": {
                "private": {
                    "smartPlannerType": "travel",
                    "smartPlannerManaged": "1",
                    "smartPlannerSourceEventId": "source-1",
                    "smartPlannerRouteMinutes": "15",
                    "smartPlannerArrivalBufferMinutes": "15",
                }
            },
        }
        events = _Events(travel, source)
        service = _Service(events)
        preferences = {
            "enabled": True,
            "default_origin": "Москва, Гиляровского 53",
            "mode": "driving",
            "arrival_buffer_minutes": 15,
        }
        estimate = RouteEstimate(
            origin="geo:55.7812000,37.6331000",
            destination="ВДНХ",
            mode="driving",
            duration_minutes=30,
            distance_meters=12000,
        )

        with (
            patch("modules.navigation_monitor.navigation_configured", return_value=True),
            patch("modules.navigation_monitor.get_user_timezone", return_value="Europe/Moscow"),
            patch("modules.navigation_monitor.get_navigation_preferences", return_value=preferences),
            patch("modules.navigation_monitor._get_calendar_service", return_value=service),
            patch("modules.navigation_monitor._fresh_live_origin", return_value=estimate.origin),
            patch("modules.navigation_monitor._resolved_event_destination", return_value="ВДНХ"),
            patch("modules.navigation_monitor.estimate_route", return_value=estimate),
            patch("modules.navigation_monitor.navigation_provider", return_value="google"),
            patch("modules.navigation_monitor.send_navigation_push_for_user", return_value=True) as push,
        ):
            stats = navigation_monitor.recalculate_user_navigation_once(1, now=now, force=True)

        self.assertEqual(stats["recalculated"], 1)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["alerts"], 1)
        self.assertEqual(len(events.patch_calls), 1)
        body = events.patch_calls[0]["body"]
        self.assertEqual(body["start"]["dateTime"], "2026-09-14T15:15:00+03:00")
        self.assertEqual(body["extendedProperties"]["private"]["smartPlannerTrafficAware"], "1")
        push.assert_called_once()
        self.assertEqual(push.call_args.kwargs["title"], "Маршрут изменился")


if __name__ == "__main__":
    unittest.main()
