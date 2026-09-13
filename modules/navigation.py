"""Navigation and managed travel blocks for calendar events.

The module is deliberately optional: calendar creation must keep working when
Yandex routing is not configured or temporarily unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
import time
from typing import Any

import requests

from config import YANDEX_GEOCODER_API_KEY, YANDEX_ROUTING_API_KEY
from core.db import get_category_colors
from core.navigation_store import get_navigation_preferences
from modules.calendar_availability import _event_end
from modules.calendar_user import _event_start, _get_calendar_service, _list_events

logger = logging.getLogger(__name__)

GEOCODER_URL = "https://geocode-maps.yandex.ru/v1/"
DISTANCE_MATRIX_URL = "https://api.routing.yandex.net/v2/distancematrix"
REQUEST_TIMEOUT_SECONDS = 8
TRAVEL_KIND = "travel"
MANAGED_VALUE = "1"


@dataclass(frozen=True)
class RouteEstimate:
    origin: str
    destination: str
    mode: str
    duration_minutes: int
    distance_meters: int | None = None


def navigation_configured() -> bool:
    return bool(YANDEX_GEOCODER_API_KEY and YANDEX_ROUTING_API_KEY)


def _private(event: dict) -> dict:
    return dict(((event.get("extendedProperties") or {}).get("private") or {}))


def is_managed_travel_event(event: dict) -> bool:
    private = _private(event)
    return private.get("smartPlannerType") == TRAVEL_KIND and private.get("smartPlannerManaged") == MANAGED_VALUE


def _source_event_id(event: dict) -> str | None:
    return str(event.get("id") or "").strip() or None


def _geocode(address: str) -> tuple[float, float]:
    if not YANDEX_GEOCODER_API_KEY:
        raise RuntimeError("YANDEX_GEOCODER_API_KEY is not configured")
    response = requests.get(
        GEOCODER_URL,
        params={
            "apikey": YANDEX_GEOCODER_API_KEY,
            "geocode": address,
            "lang": "ru_RU",
            "format": "json",
            "results": 1,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    members = (((data.get("response") or {}).get("GeoObjectCollection") or {}).get("featureMember") or [])
    if not members:
        raise ValueError(f"Location not found: {address}")
    pos = (((members[0].get("GeoObject") or {}).get("Point") or {}).get("pos") or "").split()
    if len(pos) != 2:
        raise ValueError(f"Invalid geocoder response for: {address}")
    lon, lat = float(pos[0]), float(pos[1])
    return lat, lon


def _matrix_duration_seconds(data: dict) -> tuple[int, int | None]:
    rows = data.get("rows") or []
    elements = (rows[0].get("elements") or []) if rows else []
    element = elements[0] if elements else {}
    if element.get("status") not in {None, "OK"}:
        raise ValueError(f"Route unavailable: {element.get('status')}")
    duration = element.get("duration") or {}
    raw_duration = duration.get("value") if isinstance(duration, dict) else duration
    if raw_duration is None:
        raise ValueError("Route duration is missing")
    distance = element.get("distance") or {}
    raw_distance = distance.get("value") if isinstance(distance, dict) else distance
    return int(raw_duration), int(raw_distance) if raw_distance is not None else None


def estimate_route(origin: str, destination: str, *, mode: str, departure_at: datetime) -> RouteEstimate:
    if not navigation_configured():
        raise RuntimeError("Yandex navigation is not configured")
    origin_lat, origin_lon = _geocode(origin)
    dest_lat, dest_lon = _geocode(destination)
    departure_ts = max(int(departure_at.timestamp()), int(time.time()) + 1)
    params: dict[str, Any] = {
        "apikey": YANDEX_ROUTING_API_KEY,
        "origins": f"{origin_lat},{origin_lon}",
        "destinations": f"{dest_lat},{dest_lon}",
        "mode": mode,
    }
    if mode in {"driving", "transit"}:
        params["departure_time"] = departure_ts
    response = requests.get(DISTANCE_MATRIX_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    seconds, distance = _matrix_duration_seconds(response.json())
    return RouteEstimate(
        origin=origin,
        destination=destination,
        mode=mode,
        duration_minutes=max(1, math.ceil(seconds / 60)),
        distance_meters=distance,
    )


def _previous_event_origin(user_id: int, target_event: dict, timezone: str) -> str | None:
    target_start, all_day = _event_start(target_event, timezone)
    if not target_start or all_day:
        return None
    day_start = target_start.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = _list_events(user_id, day_start, target_start)
    best_end = None
    best_location = None
    target_id = target_event.get("id")
    for event in candidates:
        if event.get("id") == target_id or is_managed_travel_event(event):
            continue
        location = str(event.get("location") or "").strip()
        if not location:
            continue
        end = _event_end(event, timezone)
        if not end or end > target_start:
            continue
        if best_end is None or end > best_end:
            best_end = end
            best_location = location
    return best_location


def resolve_origin(user_id: int, target_event: dict, timezone: str) -> str | None:
    previous = _previous_event_origin(user_id, target_event, timezone)
    if previous:
        return previous
    prefs = get_navigation_preferences(user_id)
    return str(prefs.get("default_origin") or "").strip() or None


def _travel_summary(destination: str) -> str:
    value = destination.strip()
    if len(value) > 80:
        value = value[:77].rstrip() + "..."
    return f"Дорога -> {value}"


def build_travel_event(
    source_event: dict,
    estimate: RouteEstimate,
    *,
    timezone: str,
    arrival_buffer_minutes: int,
    color_id: str | None,
) -> dict:
    source_start, all_day = _event_start(source_event, timezone)
    if not source_start or all_day:
        raise ValueError("Travel block requires a timed source event")
    total_minutes = estimate.duration_minutes + max(0, int(arrival_buffer_minutes))
    travel_end = source_start
    travel_start = travel_end - timedelta(minutes=total_minutes)
    source_id = _source_event_id(source_event)
    if not source_id:
        raise ValueError("Source event must have an id")
    private = {
        "smartPlannerType": TRAVEL_KIND,
        "smartPlannerManaged": MANAGED_VALUE,
        "smartPlannerSourceEventId": source_id,
        "smartPlannerRouteMode": estimate.mode,
        "smartPlannerRouteMinutes": str(estimate.duration_minutes),
        "smartPlannerArrivalBufferMinutes": str(max(0, int(arrival_buffer_minutes))),
    }
    event = {
        "summary": _travel_summary(estimate.destination),
        "description": (
            "AI Smart Planner category: travel\n"
            f"Маршрут: {estimate.origin} -> {estimate.destination}\n"
            f"Расчетное время: {estimate.duration_minutes} мин\n"
            f"Запас до встречи: {max(0, int(arrival_buffer_minutes))} мин"
        ),
        "location": estimate.destination,
        "start": {"dateTime": travel_start.isoformat(), "timeZone": timezone},
        "end": {"dateTime": travel_end.isoformat(), "timeZone": timezone},
        "extendedProperties": {"private": private},
        "transparency": "opaque",
    }
    if color_id:
        event["colorId"] = color_id
    return event


def _linked_travel_events(user_id: int, source_event_id: str) -> list[dict]:
    service = _get_calendar_service(user_id)
    result = service.events().list(
        calendarId="primary",
        privateExtendedProperty=[
            f"smartPlannerType={TRAVEL_KIND}",
            f"smartPlannerSourceEventId={source_event_id}",
        ],
        singleEvents=True,
        maxResults=10,
    ).execute()
    return list(result.get("items") or [])


def delete_travel_for_event(user_id: int, source_event_id: str) -> int:
    if not source_event_id:
        return 0
    service = _get_calendar_service(user_id)
    deleted = 0
    for travel in _linked_travel_events(user_id, source_event_id):
        event_id = travel.get("id")
        if not event_id:
            continue
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        deleted += 1
    return deleted


def create_travel_for_event(user_id: int, source_event: dict, timezone: str) -> dict | None:
    prefs = get_navigation_preferences(user_id)
    if not prefs.get("enabled") or not navigation_configured():
        return None
    if source_event.get("recurrence") or source_event.get("recurringEventId"):
        return None
    source_id = _source_event_id(source_event)
    destination = str(source_event.get("location") or "").strip()
    if not source_id or not destination or is_managed_travel_event(source_event):
        return None
    source_start, all_day = _event_start(source_event, timezone)
    if not source_start or all_day:
        return None
    origin = resolve_origin(user_id, source_event, timezone)
    if not origin or origin.casefold() == destination.casefold():
        return None
    estimate = estimate_route(
        origin,
        destination,
        mode=str(prefs.get("mode") or "driving"),
        departure_at=source_start,
    )
    travel = build_travel_event(
        source_event,
        estimate,
        timezone=timezone,
        arrival_buffer_minutes=int(prefs.get("arrival_buffer_minutes") or 0),
        color_id=get_category_colors(user_id).get("travel"),
    )
    service = _get_calendar_service(user_id)
    return service.events().insert(calendarId="primary", body=travel).execute()


def sync_travel_for_event(user_id: int, source_event: dict, timezone: str) -> dict | None:
    """Rebuild a linked travel block after a meeting changes.

    Navigation errors are intentionally isolated from the source calendar event.
    """
    source_id = _source_event_id(source_event)
    if not source_id:
        return None
    try:
        delete_travel_for_event(user_id, source_id)
        return create_travel_for_event(user_id, source_event, timezone)
    except Exception:
        logger.exception("Travel block sync failed for user %s event %s", user_id, source_id)
        return None


def safe_create_travel_for_event(user_id: int, source_event: dict, timezone: str) -> dict | None:
    try:
        return create_travel_for_event(user_id, source_event, timezone)
    except Exception:
        logger.exception("Travel block creation failed for user %s event %s", user_id, source_event.get("id"))
        return None


def safe_delete_travel_for_event(user_id: int, source_event_id: str) -> None:
    try:
        delete_travel_for_event(user_id, source_event_id)
    except Exception:
        logger.exception("Travel block deletion failed for user %s event %s", user_id, source_event_id)
