"""Location follow-up for newly created calendar events in the web app."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re

from flask import Blueprint, jsonify, request, session

from core.db import get_user_timezone
from core.navigation_store import get_navigation_preferences
from modules.calendar_user import _event_start, _get_calendar_service, _list_events
from modules.navigation import (
    is_managed_travel_event,
    navigation_configured,
    sync_travel_for_event,
)

calendar_location_api = Blueprint("calendar_location", __name__)
logger = logging.getLogger(__name__)
REMOTE_EVENT_RE = re.compile(
    r"\b(?:онлайн|дистанцион\w*|zoom|google\s+meet|meet|teams|видеозвон\w*|телефон\w*)\b",
    re.IGNORECASE,
)
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


def _user() -> int:
    return int(session["user_id"])


def _created_at(event: dict) -> datetime | None:
    raw = str(event.get("created") or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalise_title(value: object) -> str:
    return " ".join(str(value or "").split()).strip().casefold().replace("ё", "е")


def _pending_location_request(user_id: int, title: str, *, now: datetime | None = None) -> dict | None:
    """Return a safe follow-up only for a just-created, future, locationless event."""
    title = " ".join(str(title or "").split()).strip()
    if not title or len(title) > 200:
        return None

    preferences = get_navigation_preferences(user_id)
    if not preferences.get("enabled") or not navigation_configured():
        return None

    timezone_name = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expected = _normalise_title(title)
    candidates: list[tuple[datetime | None, datetime, dict]] = []
    try:
        events = _list_events(user_id, now_utc - timedelta(minutes=5), now_utc + timedelta(days=31))
    except Exception:
        logger.exception("Failed to search new locationless event for user %s", user_id)
        return None

    for event in events:
        if is_managed_travel_event(event) or str(event.get("location") or "").strip():
            continue
        summary = str(event.get("summary") or "").strip()
        if _normalise_title(summary) != expected or REMOTE_EVENT_RE.search(summary):
            continue
        start, all_day = _event_start(event, timezone_name)
        if all_day or not start or start.astimezone(timezone.utc) <= now_utc:
            continue
        created = _created_at(event)
        if created is not None and not timedelta(seconds=-30) <= now_utc - created <= timedelta(minutes=10):
            continue
        candidates.append((created, start.astimezone(timezone.utc), event))

    if not candidates:
        return None
    with_created = [item for item in candidates if item[0] is not None]
    if with_created:
        candidates = with_created
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    elif len(candidates) != 1:
        return None

    event = candidates[0][2]
    event_id = str(event.get("id") or "").strip()
    if not EVENT_ID_RE.fullmatch(event_id):
        return None
    start, _ = _event_start(event, timezone_name)
    return {
        "event_id": event_id,
        "title": str(event.get("summary") or title)[:200],
        "starts_at": start.isoformat() if start else None,
    }


@calendar_location_api.get("/api/mobile/calendar-location")
def pending_calendar_location():
    user_id = _user()
    title = str(request.args.get("title") or "").strip()
    pending = _pending_location_request(user_id, title)
    return {"request": pending}


@calendar_location_api.post("/api/mobile/calendar-location/<event_id>")
def save_calendar_location(event_id: str):
    user_id = _user()
    if not EVENT_ID_RE.fullmatch(event_id):
        return jsonify(error="invalid_event_id", message="Некорректное событие."), 400

    payload = request.get_json(silent=True) or {}
    if payload.get("skip") is True:
        return {"ok": True, "skipped": True}

    location = " ".join(str(payload.get("location") or "").split()).strip(" ,.;")
    if not location:
        return jsonify(error="location_required", message="Укажи адрес или место."), 400
    if len(location) > 500:
        return jsonify(error="location_too_long", message="Адрес слишком длинный."), 400

    timezone_name = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    service = _get_calendar_service(user_id)
    try:
        event = service.events().get(calendarId="primary", eventId=event_id).execute()
    except Exception:
        logger.exception("Failed to load calendar event %s for user %s", event_id, user_id)
        return jsonify(error="calendar_event_not_found", message="Событие больше не найдено."), 404
    if is_managed_travel_event(event):
        return jsonify(error="managed_travel_event", message="Нельзя менять место служебного блока дороги."), 409

    try:
        updated = service.events().patch(
            calendarId="primary",
            eventId=event_id,
            body={"location": location},
        ).execute()
    except Exception:
        logger.exception("Failed to save calendar location for user %s event %s", user_id, event_id)
        return jsonify(error="calendar_location_failed", message="Не удалось сохранить место события."), 502

    sync_travel_for_event(user_id, updated, timezone_name)
    navigation = dict(updated.get("_smartPlannerNavigation") or {})
    return {
        "ok": True,
        "location": location,
        "navigation_status": navigation.get("status"),
    }
