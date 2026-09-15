"""Master-switch enforcement for navigation and live location web features."""

from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from core.location_context import clear_current_location
from core.navigation_store import get_navigation_preferences

navigation_access_api = Blueprint("navigation_access", __name__)

_EMPTY_REQUEST_PATHS = {
    "/api/navigation/origin-request",
    "/api/navigation/optimization-request",
    "/api/mobile/calendar-location",
}
_BLOCKED_ACTION_PATHS = {
    "/api/location",
    "/api/navigation/test",
    "/api/navigation/next-route",
    "/api/navigation/origin-request",
    "/api/navigation/optimization-request",
}


def _user_id() -> int | None:
    value = session.get("user_id")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _navigation_enabled(user_id: int) -> bool:
    return get_navigation_preferences(user_id).get("enabled") is True


def _disabled_response():
    return jsonify(
        error="navigation_disabled",
        message="Навигация выключена в настройках.",
    ), 409


@navigation_access_api.before_app_request
def enforce_navigation_switch():
    user_id = _user_id()
    if user_id is None or not request.path.startswith("/api/"):
        return None

    enabled = _navigation_enabled(user_id)
    if enabled:
        return None

    if request.path in {"/api/status", "/api/location"}:
        clear_current_location(user_id)

    if request.method == "GET" and request.path in _EMPTY_REQUEST_PATHS:
        return {"request": None}

    if request.path.startswith("/api/mobile/calendar-location/"):
        return _disabled_response()

    if request.path in _BLOCKED_ACTION_PATHS:
        return _disabled_response()
    return None


@navigation_access_api.after_app_request
def clear_live_location_after_disable(response):
    if request.path != "/api/settings" or request.method != "POST" or not 200 <= response.status_code < 300:
        return response
    payload = request.get_json(silent=True) or {}
    navigation = payload.get("navigation")
    if isinstance(navigation, dict) and navigation.get("enabled") is False:
        user_id = _user_id()
        if user_id is not None:
            clear_current_location(user_id)
    return response
