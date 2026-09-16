"""Confirmed execution of planning actions proposed from read-only email analysis."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, jsonify, request, send_from_directory, session
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from core.db import get_category_colors, get_google_token, get_user_timezone
from core.reminder_store import create_reminder
from core.task_planner_store import create_planner_task
from modules.calendar import _create_event
from modules.email_actions import build_email_plan
from modules.file_ingest import ALLOWED_CATEGORIES

logger = logging.getLogger(__name__)
email_actions_api = Blueprint("email_actions", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

AUTO_CREATE_CONFIDENCE = 0.99
AUTO_CREATE_DOCUMENT_TYPES = {
    "flight_ticket",
    "boarding_pass",
    "train_ticket",
    "bus_ticket",
    "travel_ticket",
}


def _user() -> int:
    return int(session["user_id"])


def _parse_when(value: object, *, required: bool) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise ValueError("В предложении нет точной даты и времени")
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Некорректная дата в предложении") from exc
    if parsed.tzinfo is None:
        raise ValueError("Дата должна содержать часовой пояс")
    return parsed


def _valid_timezone(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Некорректный часовой пояс события") from exc
    return raw


def _clean_location(value: object) -> str:
    return " ".join(str(value or "").split()).strip(" ,.;")[:500]


def _canonical_event_time(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        return raw
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _attachment_event_key(source: object, proposal: dict) -> str | None:
    if not isinstance(source, dict):
        return None
    message_id = str(source.get("provider_message_id") or "").strip()
    attachment_id = str(source.get("attachment_id") or "").strip()
    attachment_name = str(source.get("attachment") or "").strip()
    if not message_id or not (attachment_id or attachment_name):
        return None
    identity = [
        "v2",
        str(source.get("account") or "").strip(),
        message_id,
        attachment_id or attachment_name,
        _canonical_event_time(proposal.get("start")),
        _canonical_event_time(proposal.get("end")),
    ]
    digest = hashlib.sha256("\x1f".join(identity).encode("utf-8")).hexdigest()
    return digest[:40]


def _calendar_service(user_id: int):
    token_dict = get_google_token(user_id)
    if not token_dict:
        raise PermissionError("GOOGLE_AUTH_REQUIRED")
    credentials = Credentials.from_authorized_user_info(token_dict)
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _existing_attachment_calendar_event(user_id: int, event_key: str) -> dict | None:
    service = _calendar_service(user_id)
    result = service.events().list(
        calendarId="primary",
        privateExtendedProperty=f"smartPlannerEmailAttachmentKey={event_key}",
        maxResults=2,
        showDeleted=False,
    ).execute()
    for item in result.get("items") or []:
        if item.get("status") != "cancelled":
            return item
    return None


def _calendar_item_datetime(item: dict, field: str) -> datetime | None:
    payload = item.get(field)
    if not isinstance(payload, dict):
        return None
    raw = str(payload.get("dateTime") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _location_tokens(value: object) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", str(value or ""))
        if len(token) > 1
    }


def _locations_similar(left: object, right: object) -> bool:
    a = _location_tokens(left)
    b = _location_tokens(right)
    if not a or not b:
        return True
    overlap = len(a & b) / max(1, len(a | b))
    return overlap >= 0.5


def _existing_semantic_attachment_calendar_event(user_id: int, proposal: dict) -> dict | None:
    """Find legacy email imports whose old hash changed because AI wording changed."""
    start = _parse_when(proposal.get("start"), required=True)
    end = _parse_when(proposal.get("end"), required=True)
    assert start is not None and end is not None
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    service = _calendar_service(user_id)
    result = service.events().list(
        calendarId="primary",
        privateExtendedProperty="smartPlannerType=email_attachment_import",
        timeMin=(start_utc - timedelta(minutes=5)).isoformat(),
        timeMax=(end_utc + timedelta(minutes=5)).isoformat(),
        singleEvents=True,
        maxResults=25,
        showDeleted=False,
    ).execute()
    expected_start_location = _clean_location(proposal.get("start_location"))
    expected_end_location = _clean_location(proposal.get("end_location"))
    expected_location = _clean_location(proposal.get("location"))

    for item in result.get("items") or []:
        if item.get("status") == "cancelled":
            continue
        item_start = _calendar_item_datetime(item, "start")
        item_end = _calendar_item_datetime(item, "end")
        if item_start is None or item_end is None:
            continue
        if abs((item_start - start_utc).total_seconds()) > 120:
            continue
        if abs((item_end - end_utc).total_seconds()) > 120:
            continue

        private = ((item.get("extendedProperties") or {}).get("private") or {})
        existing_start_location = private.get("smartPlannerStartLocation") or item.get("location")
        existing_end_location = private.get("smartPlannerEndLocation")
        if expected_start_location and not _locations_similar(expected_start_location, existing_start_location):
            continue
        if expected_end_location and existing_end_location and not _locations_similar(expected_end_location, existing_end_location):
            continue
        if not expected_start_location and expected_location and not _locations_similar(expected_location, item.get("location")):
            continue
        return item
    return None


def _ensure_attachment_calendar_event(
    user_id: int,
    proposal: dict,
    *,
    source: object = None,
    auto_created: bool = False,
) -> tuple[dict, bool]:
    if proposal.get("ready") is False:
        raise ValueError("Событие из вложения требует проверки перед добавлением")

    title = " ".join(str(proposal.get("title") or "").split()).strip()[:200]
    if not title:
        raise ValueError("У события из вложения нет названия")
    start = _parse_when(proposal.get("start"), required=True)
    end = _parse_when(proposal.get("end"), required=True)
    assert start is not None and end is not None
    if end <= start:
        raise ValueError("Окончание события должно быть позже начала")
    if end - start > timedelta(days=45):
        raise ValueError("Слишком большая длительность события")
    if start.astimezone(timezone.utc) < datetime.now(timezone.utc) - timedelta(minutes=5):
        raise ValueError("Нельзя добавить событие из прошлого")

    start_timezone = _valid_timezone(proposal.get("start_timezone"))
    end_timezone = _valid_timezone(proposal.get("end_timezone"))
    category = str(proposal.get("category") or "personal").strip().lower()
    if category not in ALLOWED_CATEGORIES:
        category = "personal"

    location = _clean_location(proposal.get("location"))
    start_location = _clean_location(proposal.get("start_location"))
    end_location = _clean_location(proposal.get("end_location"))
    movement = bool(proposal.get("movement") and start_location and end_location)
    if movement and proposal.get("timezone_verified") is False:
        raise ValueError("Для автоматического добавления поездки часовые пояса должны быть проверены")
    calendar_location = start_location if movement else (location or start_location or end_location)

    event_key = _attachment_event_key(source, proposal)
    existing = None
    if event_key:
        existing = _existing_attachment_calendar_event(user_id, event_key)
    if existing is None:
        existing = _existing_semantic_attachment_calendar_event(user_id, proposal)
    if existing is not None:
        return existing, False

    details = str(proposal.get("description") or "").strip()[:1500]
    creation_note = (
        "Автоматически создано из транспортного билета с уверенностью распознавания 99% или выше."
        if auto_created
        else "Создано из вложения электронной почты после подтверждения пользователя."
    )
    description = f"AI Smart Planner category: {category}\n{creation_note}"
    if movement:
        description += f"\nМаршрут: {start_location} → {end_location}"
    if details:
        description += f"\n\n{details}"

    private = {
        "smartPlannerType": "email_attachment_import",
        "smartPlannerManaged": "1",
    }
    if event_key:
        private["smartPlannerEmailAttachmentKey"] = event_key
    if auto_created:
        private["smartPlannerAutoCreated"] = "1"
    if movement:
        private["smartPlannerMovement"] = "1"
        private["smartPlannerStartLocation"] = start_location
        private["smartPlannerEndLocation"] = end_location

    event = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "transparency": "opaque",
        "extendedProperties": {"private": private},
    }
    if start_timezone:
        event["start"]["timeZone"] = start_timezone
    if end_timezone:
        event["end"]["timeZone"] = end_timezone
    if calendar_location:
        event["location"] = calendar_location
    color = get_category_colors(user_id).get(category)
    if color:
        event["colorId"] = color
    return _create_event(user_id, event), True


def _create_attachment_calendar_event(user_id: int, proposal: dict) -> dict:
    created, _ = _ensure_attachment_calendar_event(user_id, proposal)
    return created


def _auto_eligible(action: dict) -> bool:
    event = action.get("attachment_event")
    if not isinstance(event, dict):
        return False
    if action.get("action_type") != "calendar_event" or action.get("ready") is False or event.get("ready") is False:
        return False
    document_type = str(action.get("attachment_document_type") or "").strip().lower()
    if document_type not in AUTO_CREATE_DOCUMENT_TYPES:
        return False
    try:
        confidence = float(action.get("confidence") or event.get("confidence") or 0)
    except (TypeError, ValueError):
        return False
    if confidence < AUTO_CREATE_CONFIDENCE:
        return False
    if event.get("movement") and event.get("timezone_verified") is not True:
        return False
    return True


def apply_high_confidence_attachment_actions(user_id: int, plan: dict) -> dict:
    """Auto-create only validated transport tickets at or above the user's 99% policy."""
    created_count = 0
    existing_count = 0
    failed_count = 0
    actions = plan.get("actions") if isinstance(plan, dict) else None
    if not isinstance(actions, list):
        return plan

    for action in actions:
        if not isinstance(action, dict) or not _auto_eligible(action):
            continue
        try:
            event, created = _ensure_attachment_calendar_event(
                user_id,
                action["attachment_event"],
                source=action.get("source"),
                auto_created=True,
            )
            action["applied"] = True
            action["calendar_event_id"] = str(event.get("id") or "")
            if created:
                action["auto_created"] = True
                created_count += 1
            else:
                action["already_in_calendar"] = True
                existing_count += 1
        except Exception as exc:
            failed_count += 1
            action["auto_create_error"] = "Не удалось автоматически добавить билет в календарь."
            logger.warning(
                "High-confidence email ticket auto-create failed user=%s title=%s (%s)",
                user_id,
                action.get("title"),
                type(exc).__name__,
            )

    plan["auto_calendar"] = {
        "enabled": True,
        "confidence_threshold": AUTO_CREATE_CONFIDENCE,
        "created": created_count,
        "already_present": existing_count,
        "failed": failed_count,
    }
    return plan


@email_actions_api.after_app_request
def email_actions_ui_hook(response):
    if request.path == "/" and response.status_code == 200 and response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        script = '<script src="/email-actions.js"></script>'
        if script not in html and "</body>" in html:
            response.set_data(html.replace("</body>", f"    {script}\n  </body>", 1))
    return response


@email_actions_api.get("/email-actions.js")
def email_actions_js():
    return send_from_directory(WEB_DIR, "email-actions.js", mimetype="application/javascript")


@email_actions_api.post("/api/email/plan")
def email_plan():
    payload = request.get_json(silent=True) or {}
    request_text = str(payload.get("request") or "Разбери последние письма: что нужно учесть в планах?")[:1000]
    plan = build_email_plan(_user(), request_text, include_attachments=True)
    return apply_high_confidence_attachment_actions(_user(), plan)


@email_actions_api.post("/api/email/action")
def apply_email_action():
    payload = request.get_json(silent=True) or {}
    action_type = str(payload.get("action_type") or "").strip()
    title = " ".join(str(payload.get("title") or "").split()).strip()[:300]
    if action_type not in {"task", "reminder", "calendar_event"} or not title:
        raise ValueError("Некорректное действие из письма")
    user_id = _user()

    attachment_event = payload.get("attachment_event")
    if action_type == "calendar_event" and isinstance(attachment_event, dict):
        created, created_new = _ensure_attachment_calendar_event(
            user_id,
            attachment_event,
            source=payload.get("source"),
            auto_created=False,
        )
        return {
            "ok": True,
            "type": "calendar_event",
            "item": created,
            "source": "email_attachment",
            "created": created_new,
            "already_present": not created_new,
        }

    when = _parse_when(payload.get("due_at"), required=action_type != "task")
    duration = payload.get("duration_minutes")
    if duration is not None:
        if isinstance(duration, bool):
            raise ValueError("Некорректная длительность")
        try:
            duration = max(5, min(720, int(duration)))
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректная длительность") from exc

    if action_type == "task":
        task = create_planner_task(
            user_id,
            title,
            due_at=when.isoformat() if when else None,
            category="work",
            estimate_minutes=duration,
            flexible=True,
        )
        return {"ok": True, "type": "task", "item": task}

    if when is None or when <= datetime.now(when.tzinfo):
        raise ValueError("Для действия нужна будущая дата")
    if action_type == "reminder":
        reminder = create_reminder(user_id, title, when)
        return {"ok": True, "type": "reminder", "item": reminder}

    minutes = duration or 60
    end = when + timedelta(minutes=minutes)
    timezone_name = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    category = "work"
    event = {
        "summary": title[:200],
        "description": "AI Smart Planner category: work\nСоздано пользователем из предложения по письму.",
        "start": {"dateTime": when.isoformat(), "timeZone": timezone_name},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
        "transparency": "opaque",
    }
    color = get_category_colors(user_id).get(category)
    if color:
        event["colorId"] = color
    created = _create_event(user_id, event)
    return {"ok": True, "type": "calendar_event", "item": created}
