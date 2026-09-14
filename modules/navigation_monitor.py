"""Background recalculation of managed travel blocks near departure time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import threading
from time import sleep
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.db import get_user_timezone
from core.location_context import get_current_location
from core.navigation_store import get_navigation_preferences, list_navigation_user_ids
from modules.calendar_user import _event_start, _get_calendar_service
from modules.navigation import (
    MANAGED_VALUE,
    TRAVEL_KIND,
    RouteEstimate,
    _previous_event_origin,
    _resolved_event_destination,
    estimate_route,
    navigation_configured,
    navigation_provider,
)
from modules.navigation_notifications import send_navigation_push_for_user

logger = logging.getLogger(__name__)

WORKER_INTERVAL_SECONDS = 5 * 60
LOOKAHEAD_HOURS = 8
LOOKBACK_HOURS = 2
RECALC_WINDOW_MINUTES = 90
MAX_LIVE_LOCATION_ACCURACY_METERS = 500
EARLY_ALERT_THRESHOLD_MINUTES = 5
LEAVE_NOW_WINDOW_MINUTES = 3

_worker_lock = threading.Lock()
_worker_started = False
_wake_event = threading.Event()
_requested_lock = threading.RLock()
_requested_users: set[int] = set()
_last_recalc_lock = threading.RLock()
_last_recalc: dict[tuple[int, str], datetime] = {}


def request_navigation_recalculation(user_id: int) -> None:
    with _requested_lock:
        _requested_users.add(int(user_id))
    _wake_event.set()


def _take_requested_users() -> list[int]:
    with _requested_lock:
        user_ids = sorted(_requested_users)
        _requested_users.clear()
    return user_ids


def _private(event: dict) -> dict[str, str]:
    return dict(((event.get("extendedProperties") or {}).get("private") or {}))


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _travel_start(event: dict, timezone_name: str) -> datetime | None:
    start, all_day = _event_start(event, timezone_name)
    return None if all_day else start


def _fresh_live_origin(user_id: int) -> str | None:
    location = get_current_location(user_id)
    if not location:
        return None
    if (
        location.accuracy_meters is not None
        and location.accuracy_meters > MAX_LIVE_LOCATION_ACCURACY_METERS
    ):
        return None
    return f"geo:{location.latitude:.7f},{location.longitude:.7f}"


def _origin_for_recalculation(user_id: int, source_event: dict, timezone_name: str, preferences: dict) -> str | None:
    live = _fresh_live_origin(user_id)
    if live:
        return live
    previous = _previous_event_origin(user_id, source_event, timezone_name, preferences)
    if previous:
        return previous
    return str(preferences.get("default_origin") or "").strip() or None


def _traffic_aware(mode: str, departure_at: datetime, *, now: datetime) -> bool:
    if mode != "driving":
        return False
    provider = navigation_provider().casefold()
    if provider == "google":
        return True
    if provider == "2gis":
        return departure_at <= now + timedelta(minutes=15)
    return False


def _recalc_interval(scheduled_departure: datetime, now: datetime) -> timedelta:
    minutes = (scheduled_departure - now).total_seconds() / 60
    if minutes <= 15:
        return timedelta(minutes=3)
    if minutes <= 45:
        return timedelta(minutes=5)
    return timedelta(minutes=15)


def _should_recalculate(user_id: int, travel_id: str, scheduled_departure: datetime, now: datetime, *, force: bool) -> bool:
    key = (int(user_id), str(travel_id))
    with _last_recalc_lock:
        previous = _last_recalc.get(key)
        minimum = timedelta(minutes=2) if force else _recalc_interval(scheduled_departure, now)
        if previous and now - previous < minimum:
            return False
        _last_recalc[key] = now
    return True


def _managed_travel_events(user_id: int, now: datetime) -> list[dict]:
    service = _get_calendar_service(user_id)
    result = service.events().list(
        calendarId="primary",
        timeMin=(now - timedelta(hours=LOOKBACK_HOURS)).astimezone(timezone.utc).isoformat(),
        timeMax=(now + timedelta(hours=LOOKAHEAD_HOURS)).astimezone(timezone.utc).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        privateExtendedProperty=[
            f"smartPlannerType={TRAVEL_KIND}",
            f"smartPlannerManaged={MANAGED_VALUE}",
        ],
        maxResults=100,
    ).execute()
    return list(result.get("items") or [])


def _http_status(exc: Exception) -> int | None:
    response = getattr(exc, "resp", None)
    status = getattr(response, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _load_source_event(service, source_event_id: str) -> dict | None:
    try:
        return service.events().get(calendarId="primary", eventId=source_event_id).execute()
    except Exception as exc:
        if _http_status(exc) in {404, 410}:
            return None
        raise


def _event_label(source_event: dict) -> str:
    value = str(source_event.get("summary") or "Событие").strip()
    return value[:120] or "Событие"


def _format_local_time(value: datetime) -> str:
    return value.strftime("%H:%M")


def _route_description(estimate: RouteEstimate, arrival_buffer: int, traffic_aware: bool) -> str:
    traffic_text = "да" if traffic_aware else "нет"
    return (
        "AI Smart Planner category: travel\n"
        f"Маршрут: {estimate.origin} -> {estimate.destination}\n"
        f"Расчетное время: {estimate.duration_minutes} мин\n"
        f"Запас до встречи: {arrival_buffer} мин\n"
        f"Актуальные пробки: {traffic_text}"
    )


def _should_send_early_alert(old_departure: datetime, new_departure: datetime, now: datetime) -> bool:
    moved_earlier = (old_departure - new_departure).total_seconds() / 60
    return (
        moved_earlier >= EARLY_ALERT_THRESHOLD_MINUTES
        and new_departure > now + timedelta(minutes=LEAVE_NOW_WINDOW_MINUTES)
        and new_departure <= now + timedelta(minutes=60)
    )


def _departure_marker(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _already_alerted_for_departure(private: dict, new_departure: datetime) -> bool:
    previous = _parse_datetime(private.get("smartPlannerLastEarlyAlertDepartureAt"))
    if not previous:
        return False
    return abs((previous - new_departure.astimezone(timezone.utc)).total_seconds()) < EARLY_ALERT_THRESHOLD_MINUTES * 60


def _patch_travel_event(
    service,
    travel_event: dict,
    source_start: datetime,
    estimate: RouteEstimate,
    arrival_buffer: int,
    timezone_name: str,
    now: datetime,
    traffic_aware: bool,
    private: dict,
    *,
    mark_leave_alert: bool,
    mark_early_alert: bool,
) -> dict:
    new_departure = source_start - timedelta(minutes=estimate.duration_minutes + arrival_buffer)
    private = dict(private)
    private["smartPlannerRouteProvider"] = navigation_provider()
    private["smartPlannerRouteMode"] = estimate.mode
    private["smartPlannerRouteMinutes"] = str(estimate.duration_minutes)
    private["smartPlannerArrivalBufferMinutes"] = str(arrival_buffer)
    private["smartPlannerTrafficAware"] = "1" if traffic_aware else "0"
    private["smartPlannerLastRecalculatedAt"] = now.astimezone(timezone.utc).isoformat()
    private["smartPlannerDepartureAt"] = _departure_marker(new_departure)
    if mark_leave_alert:
        private["smartPlannerLeaveAlertedAt"] = now.astimezone(timezone.utc).isoformat()
    if mark_early_alert:
        private["smartPlannerLastEarlyAlertDepartureAt"] = _departure_marker(new_departure)

    patch = {
        "description": _route_description(estimate, arrival_buffer, traffic_aware),
        "start": {"dateTime": new_departure.isoformat(), "timeZone": timezone_name},
        "end": {"dateTime": source_start.isoformat(), "timeZone": timezone_name},
        "extendedProperties": {"private": private},
    }
    return service.events().patch(
        calendarId="primary",
        eventId=travel_event["id"],
        body=patch,
    ).execute()


def recalculate_user_navigation_once(user_id: int, *, now: datetime | None = None, force: bool = False) -> dict[str, int]:
    stats = {"checked": 0, "recalculated": 0, "updated": 0, "alerts": 0, "stale_deleted": 0}
    if not navigation_configured():
        return stats

    timezone_name = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("Europe/Moscow")
        timezone_name = "Europe/Moscow"

    local_now = now.astimezone(zone) if now else datetime.now(zone)
    preferences = get_navigation_preferences(user_id)
    if not preferences.get("enabled"):
        return stats

    service = _get_calendar_service(user_id)
    for travel_event in _managed_travel_events(user_id, local_now):
        stats["checked"] += 1
        travel_id = str(travel_event.get("id") or "").strip()
        private = _private(travel_event)
        source_event_id = str(private.get("smartPlannerSourceEventId") or "").strip()
        if not travel_id or not source_event_id:
            continue

        source_event = _load_source_event(service, source_event_id)
        if source_event is None:
            service.events().delete(calendarId="primary", eventId=travel_id).execute()
            stats["stale_deleted"] += 1
            continue

        source_start, all_day = _event_start(source_event, timezone_name)
        scheduled_departure = _travel_start(travel_event, timezone_name)
        if not source_start or all_day or not scheduled_departure or source_start <= local_now:
            continue
        if scheduled_departure > local_now + timedelta(minutes=RECALC_WINDOW_MINUTES):
            continue
        if not _should_recalculate(user_id, travel_id, scheduled_departure, local_now, force=force):
            continue

        destination = _resolved_event_destination(source_event, preferences)
        origin = _origin_for_recalculation(user_id, source_event, timezone_name, preferences)
        if not destination or not origin or destination.casefold() == origin.casefold():
            continue

        mode = str(preferences.get("mode") or "driving")
        estimate_departure = max(scheduled_departure, local_now + timedelta(seconds=1))
        estimate = estimate_route(
            origin,
            destination,
            mode=mode,
            departure_at=estimate_departure,
        )
        stats["recalculated"] += 1

        arrival_buffer = max(0, int(preferences.get("arrival_buffer_minutes") or 0))
        new_departure = source_start - timedelta(minutes=estimate.duration_minutes + arrival_buffer)
        traffic_aware = _traffic_aware(mode, estimate_departure, now=local_now)
        old_route_minutes = int(private.get("smartPlannerRouteMinutes") or 0)
        departure_shift_seconds = abs((new_departure - scheduled_departure).total_seconds())
        route_changed = abs(estimate.duration_minutes - old_route_minutes) >= 2
        schedule_changed = departure_shift_seconds >= 2 * 60

        leave_now = (
            new_departure <= local_now + timedelta(minutes=LEAVE_NOW_WINDOW_MINUTES)
            and not private.get("smartPlannerLeaveAlertedAt")
        )
        early_alert = (
            _should_send_early_alert(scheduled_departure, new_departure, local_now)
            and not _already_alerted_for_departure(private, new_departure)
        )

        leave_alert_sent = False
        early_alert_sent = False
        traffic_suffix = " Пробки учтены." if traffic_aware else ""
        if leave_now:
            leave_alert_sent = send_navigation_push_for_user(
                user_id,
                title="Пора выезжать",
                body=(
                    f"«{_event_label(source_event)}» в {_format_local_time(source_start)}. "
                    f"Дорога сейчас {estimate.duration_minutes} мин + {arrival_buffer} мин запас.{traffic_suffix}"
                ),
                tag=f"navigation-{source_event_id}",
            )
            stats["alerts"] += int(leave_alert_sent)
        elif early_alert:
            early_alert_sent = send_navigation_push_for_user(
                user_id,
                title="Маршрут изменился",
                body=(
                    f"На «{_event_label(source_event)}» лучше выехать в {_format_local_time(new_departure)}. "
                    f"Дорога {estimate.duration_minutes} мин + {arrival_buffer} мин запас.{traffic_suffix}"
                ),
                tag=f"navigation-{source_event_id}",
            )
            stats["alerts"] += int(early_alert_sent)

        if schedule_changed or route_changed or leave_alert_sent or early_alert_sent:
            _patch_travel_event(
                service,
                travel_event,
                source_start,
                estimate,
                arrival_buffer,
                timezone_name,
                local_now,
                traffic_aware,
                private,
                mark_leave_alert=leave_alert_sent,
                mark_early_alert=early_alert_sent,
            )
            stats["updated"] += 1

    return stats


def _worker_loop() -> None:
    while True:
        requested = _take_requested_users()
        user_ids = requested or list_navigation_user_ids()
        force = bool(requested)
        for user_id in user_ids:
            try:
                stats = recalculate_user_navigation_once(user_id, force=force)
                if stats["recalculated"] or stats["stale_deleted"]:
                    logger.info("Navigation recalculation for user %s: %s", user_id, stats)
            except Exception:
                logger.exception("Navigation recalculation failed for user %s", user_id)
        _wake_event.wait(timeout=WORKER_INTERVAL_SECONDS)
        _wake_event.clear()


def start_navigation_monitor_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(
            target=_worker_loop,
            name="navigation-monitor",
            daemon=True,
        )
        thread.start()
        _worker_started = True
