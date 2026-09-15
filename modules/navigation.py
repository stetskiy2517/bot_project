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
from core.location_context import current_location_origin
from core.navigation_store import (
    get_navigation_preferences,
    queue_navigation_origin_request,
    remove_navigation_origin_request,
)
from integrations.navigation_2gis import configured as dgis_configured, estimate as dgis_estimate
from integrations.navigation_google import configured as google_configured, estimate as google_estimate
from integrations.navigation_ors import configured as ors_configured, estimate as ors_estimate
from modules.calendar_availability import _event_end
from modules.calendar_user import _event_start, _get_calendar_service, _list_events
from modules.navigation_semantics import infer_event_end_location

logger = logging.getLogger(__name__)

TRAVEL_KIND = "travel"
MANAGED_VALUE = "1"
PREVIOUS_EVENT_MAX_GAP = timedelta(hours=1)
SAME_LOCATION_DISTANCE_METERS = 200
NATURAL_DESTINATION_RE = re.compile(
    r"\b(?:будет|пройдет|пройдёт|состоится)\s+(?:в|на)\s+(?P<location>.+?)\s*$",
    re.IGNORECASE,
)
PREPOSITIONAL_DESTINATION_RE = re.compile(
    r"\b(?:в|на)\s+(?P<location>.+?)\s*$",
    re.IGNORECASE,
)
WALK_THROUGH_DESTINATION_RE = re.compile(
    r"^\s*(?:прогул\w*|прагул\w*|погуля\w*|прогуля\w*|пробеж\w*)\s+по\s+(?P<location>.+?)\s*$",
    re.IGNORECASE,
)
BARE_ACTIVITY_DESTINATION_RE = re.compile(
    r"^\s*(?:прогул\w*|прагул\w*|погуля\w*|прогуля\w*|пробеж\w*|трениров\w*|поездк\w*|поех\w*)\s+(?P<location>.+?)\s*$",
    re.IGNORECASE,
)
TEMPORAL_DESTINATION_RE = re.compile(
    r"^(?:"
    r"сегодня|завтра|послезавтра|вчера|"
    r"понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*|"
    r"следующ\w*\s+(?:недел\w*|месяц\w*|выходн\w*)|"
    r"эт\w*\s+(?:недел\w*|месяц\w*|выходн\w*)|"
    r"выходн\w*|утр\w*|дн(?:ем|ём|я)?|вечер\w*|ноч\w*|"
    r"\d{1,2}(?:(?::|\.|-)\d{2})?|"
    r"\d+(?:[.,]\d+)?\s*(?:мин\w*|ч(?:ас\w*)?)"
    r")$",
    re.IGNORECASE,
)
NON_LOCATION_TAIL_RE = re.compile(
    r"^(?:с|со|у|для|к|от|до|через|после|перед|вместе\s+с)\b",
    re.IGNORECASE,
)
HOME_PLACE_RE = re.compile(r"\b(?:дома|домой|у\s+себя\s+дома)\b", re.IGNORECASE)
OFFICE_PLACE_RE = re.compile(
    r"\b(?:на\s+работе|на\s+работу|в\s+офисе|в\s+офис)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RouteEstimate:
    origin: str
    destination: str
    mode: str
    duration_minutes: int
    distance_meters: int | None = None


@dataclass(frozen=True)
class PreviousEventContext:
    event: dict
    end: datetime
    end_location: str | None


@dataclass(frozen=True)
class TravelWindow:
    start: datetime
    end: datetime
    required_minutes: int
    available_minutes: int | None
    missing_minutes: int


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


def _normalise_place_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip(" ,.;")


def _clean_destination_candidate(value: str, *, bare_activity: bool = False) -> str | None:
    candidate = re.sub(r"\s+", " ", value).strip(" ,.;")
    candidate = re.split(r"\s+(?:после|перед)\s+", candidate, maxsplit=1, flags=re.IGNORECASE)[0].strip(" ,.;")
    if not candidate:
        return None
    normalized = _normalise_place_text(candidate)
    if TEMPORAL_DESTINATION_RE.fullmatch(normalized):
        return None
    if bare_activity and NON_LOCATION_TAIL_RE.match(normalized):
        return None
    return candidate[:500]


def _summary_destination(summary: str) -> str | None:
    match = NATURAL_DESTINATION_RE.search(summary)
    if match:
        return _clean_destination_candidate(match.group("location"))
    match = PREPOSITIONAL_DESTINATION_RE.search(summary)
    if match:
        return _clean_destination_candidate(match.group("location"))
    match = WALK_THROUGH_DESTINATION_RE.match(summary)
    if match:
        return _clean_destination_candidate(match.group("location"), bare_activity=True)
    match = BARE_ACTIVITY_DESTINATION_RE.match(summary)
    if match:
        return _clean_destination_candidate(match.group("location"), bare_activity=True)
    return None


def _event_destination(event: dict) -> str | None:
    location = str(event.get("location") or "").strip()
    if location:
        return location
    summary = str(event.get("summary") or "").strip()
    return _summary_destination(summary)


def _place_alias_kind(value: str, *, allow_bare: bool) -> str | None:
    normalized = _normalise_place_text(value)
    if allow_bare and normalized in {"дом", "дома", "домой", "у себя дома"}:
        return "home"
    if allow_bare and normalized in {
        "работа",
        "работе",
        "работу",
        "офис",
        "офисе",
        "на работе",
        "на работу",
        "в офисе",
        "в офис",
    }:
        return "office"
    if HOME_PLACE_RE.search(normalized):
        return "home"
    if OFFICE_PLACE_RE.search(normalized):
        return "office"
    return None


def _saved_place_address(kind: str, preferences: dict) -> str | None:
    key = "home_address" if kind == "home" else "office_address"
    return str(preferences.get(key) or "").strip() or None


def _resolve_place(value: object, preferences: dict) -> str | None:
    location = " ".join(str(value or "").split()).strip(" ,.;")
    if not location:
        return None
    kind = _place_alias_kind(location, allow_bare=True)
    if kind:
        return _saved_place_address(kind, preferences)
    return location[:500]


def _resolved_event_destination(event: dict, preferences: dict) -> str | None:
    location = str(event.get("location") or "").strip()
    if location:
        return _resolve_place(location, preferences)

    summary = str(event.get("summary") or "").strip()
    value = _summary_destination(summary)
    if value:
        return _resolve_place(value, preferences)

    kind = _place_alias_kind(summary, allow_bare=False)
    if kind:
        return _saved_place_address(kind, preferences)
    return None


def _event_end_location(user_id: int, event: dict, preferences: dict) -> str | None:
    """Resolve where the user is expected to be after an event.

    Movement events use their explicit end metadata. Stationary events end at
    their normal location. Only when neither is known do we ask the AI to select
    a high-confidence saved anchor; otherwise the endpoint remains unknown.
    """
    private = _private(event)
    explicit_end = _resolve_place(private.get("smartPlannerEndLocation"), preferences)
    if explicit_end:
        return explicit_end
    if private.get("smartPlannerMovement") != "1":
        stationary = _resolved_event_destination(event, preferences)
        if stationary:
            return stationary
    return infer_event_end_location(user_id, event, preferences)


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


def _previous_event_context(
    user_id: int,
    target_event: dict,
    timezone: str,
    preferences: dict,
) -> PreviousEventContext | None:
    target_start, all_day = _event_start(target_event, timezone)
    if not target_start or all_day:
        return None
    candidates = _list_events(user_id, target_start - PREVIOUS_EVENT_MAX_GAP, target_start)
    target_id = target_event.get("id")
    best_event = None
    best_end = None
    for event in candidates:
        if event.get("id") == target_id or is_managed_travel_event(event):
            continue
        end = _event_end(event, timezone)
        if not end or end > target_start:
            continue
        gap = target_start - end
        if gap < timedelta(0) or gap > PREVIOUS_EVENT_MAX_GAP:
            continue
        if best_end is None or end > best_end:
            best_event = event
            best_end = end
    if best_event is None or best_end is None:
        return None
    return PreviousEventContext(
        event=best_event,
        end=best_end,
        end_location=_event_end_location(user_id, best_event, preferences),
    )


def _previous_event_origin(
    user_id: int,
    target_event: dict,
    timezone: str,
    preferences: dict,
) -> str | None:
    context = _previous_event_context(user_id, target_event, timezone, preferences)
    return context.end_location if context else None


def resolve_origin(
    user_id: int,
    target_event: dict,
    timezone: str,
    preferences: dict | None = None,
    *,
    prefer_live: bool = True,
) -> str | None:
    if prefer_live:
        live_origin = current_location_origin(user_id)
        if live_origin:
            return live_origin
    prefs = preferences or get_navigation_preferences(user_id)
    previous = _previous_event_origin(user_id, target_event, timezone, prefs)
    if previous:
        return previous
    return str(prefs.get("default_origin") or "").strip() or None


def _places_equivalent(left: str, right: str) -> bool:
    return _normalise_place_text(left) == _normalise_place_text(right)


def _route_is_effectively_same_place(estimate: RouteEstimate) -> bool:
    return estimate.distance_meters is not None and estimate.distance_meters <= SAME_LOCATION_DISTANCE_METERS


def travel_window(source_start: datetime, required_minutes: int, earliest_start: datetime | None = None) -> TravelWindow:
    required = max(0, int(required_minutes))
    calculated = source_start - timedelta(minutes=required)
    if earliest_start is None:
        return TravelWindow(calculated, source_start, required, None, 0)
    earliest = earliest_start.astimezone(source_start.tzinfo) if earliest_start.tzinfo else earliest_start.replace(tzinfo=source_start.tzinfo)
    if earliest >= source_start:
        return TravelWindow(source_start, source_start, required, 0, required)
    available = max(0, int((source_start - earliest).total_seconds() // 60))
    start = max(calculated, earliest)
    missing = max(0, required - available)
    return TravelWindow(start, source_start, required, available, missing)


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
    earliest_start: datetime | None = None,
    origin_source: str = "unknown",
) -> dict:
    source_start, all_day = _event_start(source_event, timezone)
    if not source_start or all_day:
        raise ValueError("Travel block requires a timed source event")
    arrival_buffer = max(0, int(arrival_buffer_minutes))
    window = travel_window(source_start, estimate.duration_minutes + arrival_buffer, earliest_start)
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
        "smartPlannerArrivalBufferMinutes": str(arrival_buffer),
        "smartPlannerOrigin": estimate.origin,
        "smartPlannerOriginSource": origin_source,
        "smartPlannerRouteConflict": "1" if window.missing_minutes else "0",
        "smartPlannerMissingMinutes": str(window.missing_minutes),
    }
    description = (
        "AI Smart Planner category: travel\n"
        f"Маршрут: {estimate.origin} -> {estimate.destination}\n"
        f"Расчетное время: {estimate.duration_minutes} мин\n"
        f"Запас до встречи: {arrival_buffer} мин"
    )
    if window.missing_minutes:
        description += (
            f"\nДоступно между событиями: {window.available_minutes or 0} мин"
            f"\nНе хватает: {window.missing_minutes} мин"
        )
    event = {
        "summary": _travel_summary(estimate.destination),
        "description": description,
        "location": estimate.destination,
        "start": {"dateTime": window.start.isoformat(), "timeZone": timezone},
        "end": {"dateTime": window.end.isoformat(), "timeZone": timezone},
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


def _navigation_status(source_event: dict, status: str, **fields) -> None:
    source_event["_smartPlannerNavigation"] = {"status": status, **fields}


def create_travel_for_event(
    user_id: int,
    source_event: dict,
    timezone: str,
    *,
    origin_override: str | None = None,
) -> dict | None:
    prefs = get_navigation_preferences(user_id)
    if not prefs.get("enabled") or not navigation_configured():
        return None
    if source_event.get("recurrence") or source_event.get("recurringEventId"):
        _navigation_status(source_event, "recurring_deferred")
        return None
    source_id = _source_event_id(source_event)
    destination = _resolved_event_destination(source_event, prefs)
    if not source_id or not destination or is_managed_travel_event(source_event):
        return None
    source_start, all_day = _event_start(source_event, timezone)
    if not source_start or all_day:
        return None

    previous = _previous_event_context(user_id, source_event, timezone, prefs)
    origin_source = "user"
    earliest_start = None
    origin = _resolve_place(origin_override, prefs) if origin_override else None
    if not origin_override:
        if previous is not None and previous.end_location:
            origin = previous.end_location
            origin_source = "previous_event"
            earliest_start = previous.end
        else:
            queue_navigation_origin_request(
                user_id,
                event_id=source_id,
                timezone_name=timezone,
                destination=destination,
                title=str(source_event.get("summary") or "Событие"),
            )
            _navigation_status(
                source_event,
                "origin_required",
                destination=destination,
                previous_event_id=(previous.event.get("id") if previous else None),
            )
            return None

    remove_navigation_origin_request(user_id, source_id)
    if not origin:
        _navigation_status(source_event, "origin_required", destination=destination)
        return None
    if _places_equivalent(origin, destination):
        _navigation_status(source_event, "same_location", origin=origin, destination=destination)
        return None

    estimate = estimate_route(
        origin,
        destination,
        mode=str(prefs.get("mode") or "driving"),
        departure_at=source_start,
    )
    if _route_is_effectively_same_place(estimate):
        _navigation_status(source_event, "same_location", origin=origin, destination=destination)
        return None

    travel = build_travel_event(
        source_event,
        estimate,
        timezone=timezone,
        arrival_buffer_minutes=int(prefs.get("arrival_buffer_minutes") or 0),
        color_id=get_category_colors(user_id).get("travel"),
        earliest_start=earliest_start,
        origin_source=origin_source,
    )
    inserted = _get_calendar_service(user_id).events().insert(calendarId="primary", body=travel).execute()
    private = _private(travel)
    missing = int(private.get("smartPlannerMissingMinutes") or 0)
    _navigation_status(
        source_event,
        "route_conflict" if missing else "created",
        origin=origin,
        destination=destination,
        missing_minutes=missing,
        travel_event_id=inserted.get("id"),
    )
    return inserted


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
        remove_navigation_origin_request(user_id, source_event_id)
    except Exception:
        logger.exception("Travel block deletion failed for user %s event %s", user_id, source_event_id)
