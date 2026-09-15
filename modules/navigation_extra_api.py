"""Extra navigation controls and origin questions for automatic routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, jsonify, request, send_from_directory, session

from core.db import get_user_timezone
from core.navigation_store import (
    get_navigation_preferences,
    list_pending_navigation_origins,
    remove_navigation_origin_request,
    save_navigation_buffers,
    save_navigation_place,
)
from modules.calendar_user import _event_start, _get_calendar_service, _list_events
from modules.navigation import (
    _resolved_event_destination,
    create_travel_for_event,
    is_managed_travel_event,
    resolve_origin,
)

navigation_extra_api = Blueprint("navigation_extra", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


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
        origin = resolve_origin(user_id, event, timezone_name, preferences=prefs, prefer_live=True)
        if not origin:
            continue
        url = "https://yandex.ru/maps/?mode=routes&rtext=" + quote(origin, safe="") + "~" + quote(destination, safe="")
        return {
            "url": url,
            "event_id": event.get("id"),
            "title": event.get("summary") or "Событие",
            "origin": origin,
            "destination": destination,
            "starts_at": start.isoformat(),
        }
    return jsonify(error="route_not_found", message="В ближайших событиях нет маршрута с указанным местом."), 404


@navigation_extra_api.errorhandler(ValueError)
def invalid_navigation_request(error):
    return jsonify(error="invalid_navigation_request", message=str(error)), 400
