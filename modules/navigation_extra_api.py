"""Extra navigation controls, origin questions and schedule optimization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import re
from urllib.parse import quote

from flask import Blueprint, jsonify, request, send_from_directory, session

from integrations.navigation_2gis import configured as dgis_configured, geocode as dgis_geocode
from integrations.navigation_ors import configured as ors_configured, geocode as ors_geocode
from core.db import get_user_timezone
from core.navigation_store import (
    get_navigation_preferences,
    list_pending_navigation_optimizations,
    list_pending_navigation_origins,
    remove_navigation_optimization_request,
    remove_navigation_origin_request,
    save_navigation_buffers,
    save_navigation_place,
)
from modules.calendar_availability import _event_end
from modules.calendar_user import _event_start, _get_calendar_service, _list_events
from modules.navigation import (
    _resolved_event_destination,
    create_travel_for_event,
    delete_travel_for_event,
    is_managed_travel_event,
    navigation_provider,
)

navigation_extra_api = Blueprint("navigation_extra", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MIN_EVENT_DURATION_MINUTES = 5
logger = logging.getLogger(__name__)
GEO_ROUTE_POINT_RE = re.compile(
    r"^(?:geo:)?(?P<lat>[+-]?\d+(?:\.\d+)?),(?P<lon>[+-]?\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)


def _yandex_route_point(value: object) -> str:
    point = " ".join(str(value or "").split()).strip()
    match = GEO_ROUTE_POINT_RE.fullmatch(point)
    if not match:
        return point
    lat = float(match.group("lat"))
    lon = float(match.group("lon"))
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return point
    return f"{lat:.7f},{lon:.7f}"


def _yandex_destination_point(value: object) -> str:
    point = " ".join(str(value or "").split()).strip()
    if not point:
        return ""
    if GEO_ROUTE_POINT_RE.fullmatch(point):
        return _yandex_route_point(point)

    provider = navigation_provider().casefold()
    geocoders = []
    if provider == "openrouteservice" and ors_configured():
        geocoders.append("ors")
    elif provider == "2gis" and dgis_configured():
        geocoders.append("2gis")
    if "ors" not in geocoders and ors_configured():
        geocoders.append("ors")
    if "2gis" not in geocoders and dgis_configured():
        geocoders.append("2gis")

    for geocoder in geocoders:
        try:
            if geocoder == "ors":
                lon, lat = ors_geocode(point)
            else:
                lat, lon = dgis_geocode(point)
            lat = float(lat)
            lon = float(lon)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return f"{lat:.7f},{lon:.7f}"
        except Exception:
            logger.warning("Failed to geocode Yandex route destination with %s: %s", geocoder, point, exc_info=True)

    return point


def _yandex_route_url(destination: object) -> str:
    finish = _yandex_route_point(destination)
    return (
        "https://yandex.ru/maps/?mode=routes&rtext=~"
        + quote(finish, safe="")
        + "&rtt=auto"
    )


def _user() -> int:
    return int(session["user_id"])


@navigation_extra_api.after_app_request
def navigation_extra_ui_hook(response):
    if request.path == "/" and response.status_code == 200 and response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        script = '<script src="/navigation-extra.js"></script>'
        if script not in html and "</body>" in html:
            response.set_data(html.replace("</body>", f"    {script}\n  </body>", 1))
    return response


@navigation_extra_api.get("/navigation-extra.js")
def navigation_extra_js():
    return send_from_directory(WEB_DIR, "navigation-extra.js", mimetype="application/javascript")


@navigation_extra_api.get("/api/navigation/buffers")
def navigation_buffers():
    prefs = get_navigation_preferences(_user())
    return {
        "base_arrival_buffer_minutes": prefs.get("base_arrival_buffer_minutes", 0),
        "parking_buffer_minutes": prefs.get("parking_buffer_minutes", 0),
        "walking_buffer_minutes": prefs.get("walking_buffer_minutes", 0),
        "arrival_buffer_minutes": prefs.get("arrival_buffer_minutes", 0),
    }


@navigation_extra_api.put("/api/navigation/buffers")
def update_navigation_buffers():
    payload = request.get_json(silent=True) or {}
    if set(payload) != {"parking_buffer_minutes", "walking_buffer_minutes"}:
        raise ValueError("Нужны буфер парковки и пеший буфер")
    values = []
    for key in ("parking_buffer_minutes", "walking_buffer_minutes"):
        value = payload.get(key)
        if isinstance(value, bool):
            raise ValueError("Буфер должен быть целым числом минут")
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Буфер должен быть целым числом минут") from exc
        if not 0 <= value <= 180:
            raise ValueError("Буфер должен быть от 0 до 180 минут")
        values.append(value)
    prefs = save_navigation_buffers(_user(), parking_minutes=values[0], walking_minutes=values[1])
    return {"preferences": prefs}


@navigation_extra_api.get("/api/navigation/origin-request")
def pending_navigation_origin():
    user_id = _user()
    prefs = get_navigation_preferences(user_id)
    service = _get_calendar_service(user_id)
    now = datetime.now(timezone.utc)
    for item in list_pending_navigation_origins(user_id):
        event_id = item["event_id"]
        try:
            event = service.events().get(calendarId="primary", eventId=event_id).execute()
        except Exception:
            remove_navigation_origin_request(user_id, event_id)
            continue
        timezone_name = item.get("timezone") or get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
        start, all_day = _event_start(event, timezone_name)
        if all_day or not start or start.astimezone(timezone.utc) <= now:
            remove_navigation_origin_request(user_id, event_id)
            continue
        destination = _resolved_event_destination(event, prefs) or item.get("destination")
        if not destination:
            remove_navigation_origin_request(user_id, event_id)
            continue
        return {
            "request": {
                "event_id": event_id,
                "title": event.get("summary") or item.get("title") or "Событие",
                "starts_at": start.isoformat(),
                "destination": destination,
                "home_available": bool(prefs.get("home_address")),
                "office_available": bool(prefs.get("office_address")),
            }
        }
    return {"request": None}


@navigation_extra_api.post("/api/navigation/origin-request")
def answer_navigation_origin():
    user_id = _user()
    payload = request.get_json(silent=True) or {}
    event_id = str(payload.get("event_id") or "").strip()
    choice = str(payload.get("choice") or "").strip().lower()
    address = " ".join(str(payload.get("address") or "").split()).strip(" ,.;")
    pending = {item["event_id"]: item for item in list_pending_navigation_origins(user_id)}
    item = pending.get(event_id)
    if item is None:
        return jsonify(error="origin_request_not_found", message="Этот вопрос уже неактуален."), 404
    if choice not in {"home", "office", "other"}:
        raise ValueError("Выбери дом, офис или другое место")

    prefs = get_navigation_preferences(user_id)
    if choice == "home":
        origin = str(prefs.get("home_address") or "").strip()
        if not origin and address:
            save_navigation_place(user_id, "home", address, make_default=False)
            origin = address
        if not origin:
            return jsonify(error="origin_address_required", place="home", message="Укажи адрес дома."), 409
    elif choice == "office":
        origin = str(prefs.get("office_address") or "").strip()
        if not origin and address:
            save_navigation_place(user_id, "office", address, make_default=False)
            origin = address
        if not origin:
            return jsonify(error="origin_address_required", place="office", message="Укажи адрес офиса."), 409
    else:
        if not address:
            return jsonify(error="origin_address_required", place="other", message="Укажи, откуда поедете."), 409
        origin = address

    service = _get_calendar_service(user_id)
    try:
        event = service.events().get(calendarId="primary", eventId=event_id).execute()
    except Exception:
        remove_navigation_origin_request(user_id, event_id)
        return jsonify(error="calendar_event_not_found", message="Событие больше не найдено в календаре."), 404

    timezone_name = item.get("timezone") or get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    create_travel_for_event(user_id, event, timezone_name, origin_override=origin)
    status = dict(event.get("_smartPlannerNavigation") or {})
    remove_navigation_origin_request(user_id, event_id)
    return {
        "ok": True,
        "status": status.get("status") or "not_created",
        "origin": origin,
        "destination": status.get("destination") or item.get("destination"),
        "missing_minutes": int(status.get("missing_minutes") or 0),
    }


def _load_event(service, event_id: str) -> dict | None:
    try:
        return service.events().get(calendarId="primary", eventId=event_id).execute()
    except Exception:
        return None


def _interval_conflict(
    user_id: int,
    timezone_name: str,
    start: datetime,
    end: datetime,
    *,
    exclude_ids: set[str],
) -> dict | None:
    for event in _list_events(user_id, start, end):
        event_id = str(event.get("id") or "")
        if event_id in exclude_ids or is_managed_travel_event(event) or event.get("transparency") == "transparent":
            continue
        event_start, all_day = _event_start(event, timezone_name)
        event_end = _event_end(event, timezone_name)
        if all_day or not event_start or not event_end:
            continue
        if event_start < end and event_end > start:
            return event
    return None


def _optimization_snapshot(user_id: int, item: dict, service) -> dict | None:
    timezone_name = item.get("timezone") or get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    target = _load_event(service, item["event_id"])
    previous = _load_event(service, item["previous_event_id"])
    if target is None or previous is None:
        return None

    target_start, target_all_day = _event_start(target, timezone_name)
    target_end = _event_end(target, timezone_name)
    previous_start, previous_all_day = _event_start(previous, timezone_name)
    previous_end = _event_end(previous, timezone_name)
    if target_all_day or previous_all_day or not target_start or not target_end or not previous_start or not previous_end:
        return None
    if target_end <= target_start or previous_end <= previous_start:
        return None

    required = max(0, int(item.get("required_minutes") or 0))
    available = max(0, int((target_start - previous_end).total_seconds() // 60)) if previous_end <= target_start else 0
    missing = max(0, required - available)
    shorten_end = previous_end - timedelta(minutes=missing)
    minimum_previous_end = previous_start + timedelta(minutes=MIN_EVENT_DURATION_MINUTES)
    can_shorten = missing > 0 and shorten_end >= minimum_previous_end

    shifted_start = target_start + timedelta(minutes=missing)
    shifted_end = target_end + timedelta(minutes=missing)
    shift_conflict = None
    if missing > 0:
        shift_conflict = _interval_conflict(
            user_id,
            timezone_name,
            shifted_start,
            shifted_end,
            exclude_ids={str(target.get("id") or ""), str(previous.get("id") or "")},
        )

    return {
        "item": item,
        "target": target,
        "previous": previous,
        "timezone": timezone_name,
        "target_start": target_start,
        "target_end": target_end,
        "previous_start": previous_start,
        "previous_end": previous_end,
        "required_minutes": required,
        "available_minutes": available,
        "missing_minutes": missing,
        "shorten_end": shorten_end,
        "can_shorten": can_shorten,
        "shifted_start": shifted_start,
        "shifted_end": shifted_end,
        "shift_conflict": shift_conflict,
    }


def _optimization_payload(snapshot: dict) -> dict:
    target = snapshot["target"]
    previous = snapshot["previous"]
    conflict = snapshot.get("shift_conflict")
    return {
        "event_id": target.get("id"),
        "previous_event_id": previous.get("id"),
        "title": target.get("summary") or snapshot["item"].get("title") or "Событие",
        "previous_title": previous.get("summary") or snapshot["item"].get("previous_title") or "Предыдущее событие",
        "required_minutes": snapshot["required_minutes"],
        "available_minutes": snapshot["available_minutes"],
        "missing_minutes": snapshot["missing_minutes"],
        "previous_end": snapshot["previous_end"].isoformat(),
        "target_start": snapshot["target_start"].isoformat(),
        "shorten": {
            "available": bool(snapshot["can_shorten"]),
            "minutes": snapshot["missing_minutes"],
            "new_end": snapshot["shorten_end"].isoformat() if snapshot["can_shorten"] else None,
        },
        "shift": {
            "available": conflict is None and snapshot["missing_minutes"] > 0,
            "minutes": snapshot["missing_minutes"],
            "new_start": snapshot["shifted_start"].isoformat(),
            "new_end": snapshot["shifted_end"].isoformat(),
            "conflict_title": (conflict.get("summary") or "Другое событие") if conflict else None,
        },
    }


@navigation_extra_api.get("/api/navigation/optimization-request")
def pending_navigation_optimization():
    user_id = _user()
    service = _get_calendar_service(user_id)
    now = datetime.now(timezone.utc)
    for item in list_pending_navigation_optimizations(user_id):
        snapshot = _optimization_snapshot(user_id, item, service)
        if snapshot is None:
            remove_navigation_optimization_request(user_id, item["event_id"])
            continue
        if snapshot["target_start"].astimezone(timezone.utc) <= now:
            remove_navigation_optimization_request(user_id, item["event_id"])
            continue
        if snapshot["missing_minutes"] <= 0:
            try:
                delete_travel_for_event(user_id, item["event_id"])
                create_travel_for_event(
                    user_id,
                    snapshot["target"],
                    snapshot["timezone"],
                    origin_override=item.get("origin"),
                )
            except Exception:
                logger.exception("Failed to reconcile resolved navigation conflict for user %s", user_id)
            remove_navigation_optimization_request(user_id, item["event_id"])
            continue
        return {"request": _optimization_payload(snapshot)}
    return {"request": None}


@navigation_extra_api.post("/api/navigation/optimization-request")
def answer_navigation_optimization():
    user_id = _user()
    payload = request.get_json(silent=True) or {}
    event_id = str(payload.get("event_id") or "").strip()
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"shorten_previous", "shift_target", "ignore_transfer"}:
        raise ValueError("Выбери один из вариантов оптимизации")

    pending = {item["event_id"]: item for item in list_pending_navigation_optimizations(user_id)}
    item = pending.get(event_id)
    if item is None:
        return jsonify(error="optimization_request_not_found", message="Это предложение уже неактуально."), 404

    service = _get_calendar_service(user_id)
    snapshot = _optimization_snapshot(user_id, item, service)
    if snapshot is None:
        remove_navigation_optimization_request(user_id, event_id)
        return jsonify(error="calendar_event_not_found", message="Одно из событий больше не найдено в календаре."), 404

    if action == "ignore_transfer":
        try:
            delete_travel_for_event(user_id, event_id)
        except Exception:
            logger.exception("Failed to delete ignored transfer for user %s event %s", user_id, event_id)
        remove_navigation_optimization_request(user_id, event_id)
        return {
            "ok": True,
            "status": "ignored",
            "message": "Оставил события как есть. Трансфер не создаю.",
        }

    if snapshot["missing_minutes"] <= 0:
        try:
            delete_travel_for_event(user_id, event_id)
            create_travel_for_event(
                user_id,
                snapshot["target"],
                snapshot["timezone"],
                origin_override=item.get("origin"),
            )
            remove_navigation_optimization_request(user_id, event_id)
            return {"ok": True, "status": "resolved", "message": "Конфликт уже устранён. Трансфер добавлен."}
        except Exception:
            logger.exception("Failed to create transfer after resolved conflict for user %s", user_id)
            return jsonify(error="navigation_optimization_failed", message="Не удалось создать трансфер."), 502

    try:
        if action == "shorten_previous":
            if not snapshot["can_shorten"]:
                return jsonify(
                    error="previous_event_too_short",
                    message="Предыдущее событие нельзя сократить настолько безопасно. Выбери другой вариант.",
                ), 409
            previous = snapshot["previous"]
            end_timezone = str(((previous.get("end") or {}).get("timeZone") or snapshot["timezone"]))
            service.events().patch(
                calendarId="primary",
                eventId=previous["id"],
                body={"end": {"dateTime": snapshot["shorten_end"].isoformat(), "timeZone": end_timezone}},
            ).execute()
            target = _load_event(service, event_id) or snapshot["target"]
            delete_travel_for_event(user_id, event_id)
            create_travel_for_event(
                user_id,
                target,
                snapshot["timezone"],
                origin_override=item.get("origin"),
            )
            remove_navigation_optimization_request(user_id, event_id)
            return {
                "ok": True,
                "status": "previous_shortened",
                "message": f"Сократил предыдущее событие до {snapshot['shorten_end'].strftime('%H:%M')}. Трансфер добавлен.",
            }

        if snapshot["shift_conflict"] is not None:
            return jsonify(
                error="shift_creates_conflict",
                message=f"Сдвиг создаст конфликт с «{snapshot['shift_conflict'].get('summary') or 'другим событием'}». Выбери другой вариант.",
            ), 409
        target = snapshot["target"]
        start_timezone = str(((target.get("start") or {}).get("timeZone") or snapshot["timezone"]))
        end_timezone = str(((target.get("end") or {}).get("timeZone") or snapshot["timezone"]))
        updated = service.events().patch(
            calendarId="primary",
            eventId=target["id"],
            body={
                "start": {"dateTime": snapshot["shifted_start"].isoformat(), "timeZone": start_timezone},
                "end": {"dateTime": snapshot["shifted_end"].isoformat(), "timeZone": end_timezone},
            },
        ).execute()
        delete_travel_for_event(user_id, event_id)
        create_travel_for_event(
            user_id,
            updated,
            snapshot["timezone"],
            origin_override=item.get("origin"),
        )
        remove_navigation_optimization_request(user_id, event_id)
        return {
            "ok": True,
            "status": "target_shifted",
            "message": f"Сдвинул начало события на {snapshot['shifted_start'].strftime('%H:%M')}. Трансфер добавлен.",
        }
    except Exception:
        logger.exception("Navigation optimization failed for user %s event %s", user_id, event_id)
        return jsonify(error="navigation_optimization_failed", message="Не удалось применить оптимизацию календаря."), 502


@navigation_extra_api.get("/api/navigation/next-route")
def next_route():
    user_id = _user()
    prefs = get_navigation_preferences(user_id)
    if not prefs.get("enabled"):
        return jsonify(error="navigation_disabled", message="Навигация выключена."), 409
    timezone_name = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    now = datetime.now(timezone.utc)
    for event in _list_events(user_id, now, now + timedelta(hours=24)):
        if is_managed_travel_event(event):
            continue
        start, all_day = _event_start(event, timezone_name)
        if not start or all_day or start <= now.astimezone(start.tzinfo):
            continue
        destination = _resolved_event_destination(event, prefs)
        if not destination:
            continue
        destination_point = _yandex_destination_point(destination)
        url = _yandex_route_url(destination_point)
        return {
            "url": url,
            "event_id": event.get("id"),
            "title": event.get("summary") or "Событие",
            "origin": "current_location",
            "destination": destination,
            "destination_point": destination_point,
            "starts_at": start.isoformat(),
        }
    return jsonify(error="route_not_found", message="В ближайших событиях нет маршрута с указанным местом."), 404


@navigation_extra_api.errorhandler(ValueError)
def invalid_navigation_request(error):
    return jsonify(error="invalid_navigation_request", message=str(error)), 400
