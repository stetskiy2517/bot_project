"""Confirmed execution of planning actions proposed from read-only email analysis."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, jsonify, request, send_from_directory, session

from core.db import get_category_colors, get_user_timezone
from core.reminder_store import create_reminder
from core.task_planner_store import create_planner_task
from modules.calendar import _create_event
from modules.email_actions import build_email_plan
from modules.file_ingest import ALLOWED_CATEGORIES

logger = logging.getLogger(__name__)
email_actions_api = Blueprint("email_actions", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


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


def _create_attachment_calendar_event(user_id: int, proposal: dict) -> dict:
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
    calendar_location = start_location if movement else (location or start_location or end_location)

    details = str(proposal.get("description") or "").strip()[:1500]
    description = (
        f"AI Smart Planner category: {category}\n"
        "Создано из вложения электронной почты после подтверждения пользователя."
    )
    if movement:
        description += f"\nМаршрут: {start_location} → {end_location}"
    if details:
        description += f"\n\n{details}"

    private = {
        "smartPlannerType": "email_attachment_import",
        "smartPlannerManaged": "1",
    }
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
    return _create_event(user_id, event)


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
    return build_email_plan(_user(), request_text, include_attachments=True)


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
        created = _create_attachment_calendar_event(user_id, attachment_event)
        return {"ok": True, "type": "calendar_event", "item": created, "source": "email_attachment"}

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
