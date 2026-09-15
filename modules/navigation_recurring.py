"""Create managed travel blocks for upcoming instances of recurring meetings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import threading

from core.db import get_category_colors, get_user_timezone
from core.navigation_store import get_navigation_preferences, list_navigation_user_ids
from modules.calendar_user import _event_start, _get_calendar_service, _list_events
from modules.navigation import (
    _is_movement_source_event,
    _linked_travel_events,
    _movement_access_destination,
    _places_equivalent,
    _previous_event_context,
    _resolved_event_destination,
    _route_is_effectively_same_place,
    build_travel_event,
    estimate_route,
    is_managed_travel_event,
    navigation_configured,
)

logger = logging.getLogger(__name__)
LOOKAHEAD_HOURS = 48
WORKER_INTERVAL_SECONDS = 15 * 60
_worker_lock = threading.Lock()
_worker_started = False


def sync_recurring_travel_for_user(user_id: int, *, now: datetime | None = None) -> int:
    if not navigation_configured():
        return 0
    prefs = get_navigation_preferences(user_id)
    if not prefs.get("enabled"):
        return 0
    timezone_name = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    current = now or datetime.now(timezone.utc)
    events = _list_events(user_id, current, current + timedelta(hours=LOOKAHEAD_HOURS))
    created_count = 0
    for event in events:
        if not event.get("recurringEventId") or is_managed_travel_event(event):
            continue
        source_id = str(event.get("id") or "").strip()
        if not source_id or _linked_travel_events(user_id, source_id):
            continue
        start, all_day = _event_start(event, timezone_name)
        if not start or all_day or start <= current.astimezone(start.tzinfo):
            continue
        if _is_movement_source_event(event):
            destination = _movement_access_destination(event, prefs)
            if not destination:
                continue
        else:
            destination = _resolved_event_destination(event, prefs)
        if not destination:
            continue

        previous = _previous_event_context(user_id, event, timezone_name, prefs)
        if previous is None or not previous.end_location or previous.end >= start:
            # A background worker cannot safely guess a start point. Interactive
            # event creation queues a Home / Office / Other question instead.
            continue
        origin = previous.end_location
        if _places_equivalent(origin, destination):
            continue
        try:
            estimate = estimate_route(
                origin,
                destination,
                mode=str(prefs.get("mode") or "driving"),
                departure_at=start,
            )
            if _route_is_effectively_same_place(estimate):
                continue
            travel = build_travel_event(
                event,
                estimate,
                timezone=timezone_name,
                arrival_buffer_minutes=int(prefs.get("arrival_buffer_minutes") or 0),
                color_id=get_category_colors(user_id).get("travel"),
                earliest_start=previous.end,
                origin_source="previous_event",
            )
            _get_calendar_service(user_id).events().insert(calendarId="primary", body=travel).execute()
            created_count += 1
        except Exception:
            logger.exception("Recurring navigation block failed user=%s event=%s", user_id, source_id)
    return created_count


def _worker_loop() -> None:
    while True:
        for user_id in list_navigation_user_ids():
            try:
                count = sync_recurring_travel_for_user(user_id)
                if count:
                    logger.info("Created %s recurring travel blocks for user %s", count, user_id)
            except Exception:
                logger.exception("Recurring navigation scan failed for user %s", user_id)
        threading.Event().wait(WORKER_INTERVAL_SECONDS)


def start_navigation_recurring_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, name="navigation-recurring", daemon=True).start()
        _worker_started = True
