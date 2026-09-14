"""Navigation and managed travel blocks for calendar events.

Navigation is optional: routing failures must never break normal calendar actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import re

from config import NAVIGATION_PROVIDER
from core.db import get_category_colors
from core.navigation_store import get_navigation_preferences
from integrations.navigation_2gis import configured as dgis_configured, estimate as dgis_estimate
from integrations.navigation_google import configured as google_configured, estimate as google_estimate
from integrations.navigation_ors import configured as ors_configured, estimate as ors_estimate
from modules.calendar_availability import _event_end
from modules.calendar_user import _event_start, _get_calendar_service, _list_events

logger = logging.getLogger(__name__)

TRAVEL_KIND = "travel"
MANAGED_VALUE = "1"
NATURAL_DESTINATION_RE = re.compile(
    r"\b(?:будет|пройдет|пройдёт|состоится)\s+(?:в|на)\s+(?P<location>.+?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RouteEstimate:
    origin: str
    destination: str
    mode: str
    duration_minutes: int
    distance_meters: int | None = None


def navigation_provider() -> str:
    provider = (NAVIGATION_PROVIDER or "ors").strip().lower()
    if provider in {"ors", "openrouteservice"}:
        return "OpenRouteService"
    return provider


def navigation_configured() -> bool:
    provider = navigation_provider().casefold()
    if provider == "openrouteservice":
        return ors_configured()
    if provider == "google":
        return google_configured()
    if provider == "2gis":
        return dgis_configured()
    return False


def _private(event: dict) -> dict:
    return dict(((event.get("extendedProperties") or {}).get("private") or {}))


def is_managed_travel_event(event: dict) -> bool:
    private = _private(event)
    return private.get("smartPlannerType") == TRAVEL_KIND and private.get("smartPlannerManaged") == MANAGED_VALUE


def _source_event_id(event: dict) -> str | None:
    return str(event.get("id") or "").strip() or None


def _event_destination(event: dict) -> str | None:
    location = str(event.get("location") or "").strip()
    if location:
        return location
    summary = str(event.get("summary") or "").strip()
    match = NATURAL_DESTINATION_RE.search(summary)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group("location")).strip(" ,.;")
    return value[:500] if value else None


def estimate_route(origin: str, destination: str, *, mode: str, departure_at: datetime) -> RouteEstimate:
    provider = navigation_provider().casefold()
    if provider in {"ors", "openrouteservice"}:
        if not ors_configured():
            raise RuntimeError("openrouteservice navigation is not configured")
        duration_minutes, distance_meters = ors_estimate(
            origin,
            destination,
            mode=mode,
            departure_at=departure_at,
        )
    elif provider == "google":
        if not google_configured():
            raise RuntimeError("Google navigation is not configured")
        duration_minutes, distance_meters = google_estimate(
            origin,
            destination,
            mode=mode,
            departure_at=departure_at,
        )
    elif provider == "2gis":
        if not dgis_configured():
            raise RuntimeError("2GIS navigation is not configured")
        duration_minutes, distance_meters = dgis_estimate(
            origin,
            destination,
            mode=mode,
            departure_at=departure_at,
        )
    else:
        raise RuntimeError(f"Unsupported navigation provider: {provider}")
    return RouteEstimate(
        origin=origin,
        destination=destination,
        mode=mode,
        duration_minutes=duration_minutes,
        distance_meters=distance_meters,
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
        location = _event_destination(event)
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
        "smartPlannerRouteProvider": navigation_provider(),
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
    destination = _event_destination(source_event)
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
