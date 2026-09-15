"""Extra navigation controls: parking/walking buffers and an explicit route link."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, jsonify, request, send_from_directory, session

from core.db import get_user_timezone
from core.navigation_store import get_navigation_preferences, save_navigation_buffers
from modules.calendar_user import _event_start, _list_events
from modules.navigation import _resolved_event_destination, is_managed_travel_event, resolve_origin

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
        # Official Yandex Maps route links accept rtext as point1~point2. Addresses are URL encoded.
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
