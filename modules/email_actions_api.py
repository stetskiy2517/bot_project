"""Confirmed execution of planning actions proposed from read-only email analysis."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory, session

from core.db import get_category_colors, get_user_timezone
from core.reminder_store import create_reminder
from core.task_planner_store import create_planner_task
from modules.calendar import _create_event
from modules.email_actions import build_email_plan

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
    return build_email_plan(_user(), request_text)


@email_actions_api.post("/api/email/action")
def apply_email_action():
    payload = request.get_json(silent=True) or {}
    action_type = str(payload.get("action_type") or "").strip()
    title = " ".join(str(payload.get("title") or "").split()).strip()[:300]
    if action_type not in {"task", "reminder", "calendar_event"} or not title:
        raise ValueError("Некорректное действие из письма")
    user_id = _user()
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
