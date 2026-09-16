"""Compact mobile UI shell and structured Today data."""

from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta, timezone
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, request, send_from_directory, session

from core.attention_store import dismiss_attention_item, mark_attention_seen
from core.db import get_user_timezone
from core.task_planner_store import list_planner_tasks, task_summary
from modules.attention import attention_snapshot
from modules.calendar_location_api import calendar_location_api
from modules.calendar_user import _event_start, _list_events
from modules.daily_review import build_day_review, capture_review_attention
from modules.file_ingest_api import file_ingest_api
from modules.navigation import is_managed_travel_event
from modules.reminder_detail_api import reminder_detail_api

mobile_ui_api = Blueprint("mobile_ui", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
logger = logging.getLogger(__name__)


def _user() -> int:
    return int(session["user_id"])


def _iso_due(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _task_payload(task: dict, *, now_utc: datetime) -> dict:
    due = _iso_due(task.get("due_at"))
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "status": task["status"],
        "priority": task.get("priority") or "normal",
        "category": task.get("category") or "other",
        "due_at": task.get("due_at"),
        "estimate_minutes": task.get("estimate_minutes"),
        "flexible": bool(task.get("flexible")),
        "calendar_event_id": task.get("calendar_event_id"),
        "scheduled_start": task.get("scheduled_start"),
        "parent_task_id": task.get("parent_task_id"),
        "repeat_rule": task.get("repeat_rule"),
        "overdue": bool(due and due < now_utc),
    }


def _event_payload(event: dict, timezone_name: str) -> dict:
    start, all_day = _event_start(event, timezone_name)
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    return {
        "id": event.get("id"),
        "title": str(event.get("summary") or "Событие")[:160],
        "starts_at": start.isoformat() if start else None,
        "all_day": bool(all_day),
        "location": str(event.get("location") or "")[:240],
        "color_id": event.get("colorId"),
        "managed_type": private.get("smartPlannerType"),
        "is_travel": is_managed_travel_event(event),
    }


@mobile_ui_api.after_app_request
def inject_mobile_ui(response):
    if request.path != "/" or response.status_code != 200 or response.mimetype != "text/html":
        return response
    html = response.get_data(as_text=True)
    stylesheets = (
        '<link rel="stylesheet" href="/mobile-ui.css" />',
        '<link rel="stylesheet" href="/mobile-ui-overlays.css" />',
        '<link rel="stylesheet" href="/attention-center.css" />',
    )
    scripts = (
        '<script src="/mobile-ui.js"></script>',
        '<script src="/mobile-ui-fixes.js"></script>',
        '<script src="/swipe-navigation.js"></script>',
        '<script src="/file-ingest.js"></script>',
        '<script src="/attention-center.js"></script>',
        '<script src="/reminder-editor.js"></script>',
    )
    if "</head>" in html:
        for stylesheet in stylesheets:
            if stylesheet not in html:
                html = html.replace("</head>", f"    {stylesheet}\n  </head>", 1)
    if "</body>" in html:
        for script in scripts:
            if script not in html:
                html = html.replace("</body>", f"    {script}\n  </body>", 1)
    response.set_data(html)
    return response


@mobile_ui_api.get("/mobile-ui.css")
def mobile_ui_css():
    return send_from_directory(WEB_DIR, "mobile-ui.css", mimetype="text/css")


@mobile_ui_api.get("/mobile-ui-overlays.css")
def mobile_ui_overlays_css():
    return send_from_directory(WEB_DIR, "mobile-ui-overlays.css", mimetype="text/css")


@mobile_ui_api.get("/attention-center.css")
def attention_center_css():
    return send_from_directory(WEB_DIR, "attention-center.css", mimetype="text/css")


@mobile_ui_api.get("/mobile-ui.js")
def mobile_ui_js():
    return send_from_directory(WEB_DIR, "mobile-ui.js", mimetype="application/javascript")


@mobile_ui_api.get("/mobile-ui-fixes.js")
def mobile_ui_fixes_js():
    return send_from_directory(WEB_DIR, "mobile-ui-fixes.js", mimetype="application/javascript")


@mobile_ui_api.get("/swipe-navigation.js")
def swipe_navigation_js():
    return send_from_directory(WEB_DIR, "swipe-navigation.js", mimetype="application/javascript")


@mobile_ui_api.get("/attention-center.js")
def attention_center_js():
    return send_from_directory(WEB_DIR, "attention-center.js", mimetype="application/javascript")


@mobile_ui_api.get("/api/mobile/attention")
def mobile_attention():
    return {"items": attention_snapshot(_user(), limit=20)}


@mobile_ui_api.post("/api/mobile/attention/<int:attention_id>/seen")
def mobile_attention_seen(attention_id: int):
    return {"ok": mark_attention_seen(_user(), attention_id)}


@mobile_ui_api.delete("/api/mobile/attention/<int:attention_id>")
def mobile_attention_dismiss(attention_id: int):
    return {"ok": dismiss_attention_item(_user(), attention_id)}


@mobile_ui_api.get("/api/mobile/today")
def mobile_today():
    user_id = _user()
    timezone_name = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    zone = ZoneInfo(timezone_name)
    now_utc = datetime.now(timezone.utc)
    local_now = now_utc.astimezone(zone)
    day_start = datetime.combine(local_now.date(), dt_time.min, tzinfo=zone)
    day_end = day_start + timedelta(days=1)
    review_kind = "evening" if local_now.hour >= 18 else "morning"

    calendar_ok = True
    events: list[dict] = []
    try:
        raw_events = _list_events(user_id, day_start, day_end)
        events = [_event_payload(item, timezone_name) for item in raw_events]
        events.sort(key=lambda item: item.get("starts_at") or "")
    except Exception:
        calendar_ok = False

    tasks = list_planner_tasks(user_id, status="open", limit=500)
    root_tasks = [task for task in tasks if task.get("parent_task_id") is None]
    task_items = [_task_payload(task, now_utc=now_utc) for task in root_tasks]

    def task_rank(item: dict) -> tuple:
        priority = {"high": 0, "normal": 1, "low": 2}.get(item.get("priority"), 1)
        due = _iso_due(item.get("due_at"))
        return (
            0 if item.get("overdue") else 1,
            priority,
            due or datetime.max.replace(tzinfo=timezone.utc),
            int(item["task_id"]),
        )

    task_items.sort(key=task_rank)
    try:
        review = build_day_review(user_id, review_kind, now=now_utc)
        try:
            capture_review_attention(user_id, review, kind=review_kind, now=now_utc)
        except Exception as exc:
            logger.warning("Failed to capture review attention for user %s (%s)", user_id, type(exc).__name__)
    except Exception:
        review = {
            "kind": review_kind,
            "date": str(local_now.date()),
            "calendar_ok": calendar_ok,
            "text": "Не удалось собрать обзор целиком. Календарь и задачи доступны отдельно.",
            "reminders": [],
        }
    try:
        attention = attention_snapshot(user_id, now=now_utc, limit=8)
    except Exception:
        attention = []

    return {
        "date": str(local_now.date()),
        "timezone": timezone_name,
        "calendar_ok": calendar_ok,
        "events": events[:20],
        "tasks": task_items[:8],
        "task_summary": task_summary(user_id, now=now_utc),
        "review": review,
        "attention": attention,
    }


mobile_ui_api.register_blueprint(calendar_location_api)
mobile_ui_api.register_blueprint(file_ingest_api)
mobile_ui_api.register_blueprint(reminder_detail_api)
