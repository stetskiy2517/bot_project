"""Mobile event details, editing, conflict handling and deletion."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

from flask import Blueprint, request, session

from core.category_store import get_category_colors, get_user_categories
from core.db import get_user_timezone
from modules import calendar as calendar_module
from modules.calendar_actions import _find_conflicts
from modules.calendar_availability import suggest_alternatives
from modules.calendar_recurrence import split_recurring_series_for_update, trim_recurring_series_from
from modules.calendar_user import _get_calendar_service
from modules.category_api import category_api
from modules.navigation import (
    is_managed_travel_event,
    safe_delete_travel_for_event,
    sync_travel_for_event,
)


event_detail_api = Blueprint("event_detail_api", __name__)

CATEGORY_RE = re.compile(r"(?im)^AI Smart Planner category:\s*([a-z][a-z0-9_-]{0,63})\s*$")
RECURRENCE_RULES = {
    "daily": "RRULE:FREQ=DAILY",
    "weekdays": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "weekends": "RRULE:FREQ=WEEKLY;BYDAY=SA,SU",
    "weekly": "RRULE:FREQ=WEEKLY",
    "monthly": "RRULE:FREQ=MONTHLY",
    "yearly": "RRULE:FREQ=YEARLY",
}
REMINDER_VALUES = {"default", "none", "custom", "10", "15", "30", "60", "1440"}


def _user_id() -> int:
    return int(session["user_id"])


def _timezone(user_id: int) -> str:
    return get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"


def _event_part_datetime(part: dict | None, timezone_name: str) -> tuple[datetime | None, bool]:
    part = part or {}
    zone = ZoneInfo(timezone_name)
    if part.get("dateTime"):
        parsed = datetime.fromisoformat(str(part["dateTime"]).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(zone), False
    if part.get("date"):
        parsed_date = date.fromisoformat(str(part["date"]))
        return datetime.combine(parsed_date, datetime.min.time(), tzinfo=zone), True
    return None, False


def _event_interval(event: dict, timezone_name: str) -> tuple[datetime, datetime, bool]:
    start, all_day = _event_part_datetime(event.get("start"), timezone_name)
    end, end_all_day = _event_part_datetime(event.get("end"), timezone_name)
    if not start or not end or all_day != end_all_day or end <= start:
        raise ValueError("Событие содержит некорректное время")
    return start, end, all_day


def _category(event: dict, user_id: int) -> str:
    categories = get_user_categories(user_id)
    active = {item["key"] for item in categories}
    colors = get_category_colors(user_id)
    description = str(event.get("description") or "")
    match = CATEGORY_RE.search(description)
    if match:
        key = match.group(1).lower()
        return key if key in active else "uncategorized"
    color_id = str(event.get("colorId") or "")
    if color_id:
        for key, value in colors.items():
            if value and str(value) == color_id:
                return key
    text = "\n".join(
        part for part in (str(event.get("summary") or ""), description) if part.strip()
    )
    detected = calendar_module._detect_category(text, colors)[0] if text else "uncategorized"
    return detected if detected in active else "uncategorized"


def _description_with_category(description: object, category: str) -> str:
    current = str(description or "").strip()
    line = f"AI Smart Planner category: {category}"
    if CATEGORY_RE.search(current):
        return CATEGORY_RE.sub(line, current)
    return f"{current}\n{line}".strip()


def _recurrence_key(recurrence: list[str] | None) -> str:
    rules = [str(item).upper() for item in (recurrence or []) if str(item).upper().startswith("RRULE:")]
    if len(rules) != 1:
        return "custom" if rules else "none"
    rule = rules[0]
    base = rule.split(";UNTIL=", 1)[0].split(";COUNT=", 1)[0]
    for key, value in RECURRENCE_RULES.items():
        if base == value:
            return key
    return "custom"


def _reminder_key(event: dict) -> str:
    reminders = event.get("reminders") or {}
    if reminders.get("useDefault") is True:
        return "default"
    overrides = reminders.get("overrides") or []
    popup = [item for item in overrides if item.get("method") == "popup" and isinstance(item.get("minutes"), int)]
    if not overrides:
        return "none"
    if len(overrides) == 1 and len(popup) == 1:
        value = str(popup[0]["minutes"])
        return value if value in REMINDER_VALUES else "custom"
    return "custom"


def _attendee_emails(event: dict) -> list[str]:
    values: list[str] = []
    for item in event.get("attendees") or []:
        email = str(item.get("email") or "").strip().lower()
        if email and email not in values:
            values.append(email)
    return values


def _load_parent(service, event: dict) -> dict | None:
    parent_id = event.get("recurringEventId")
    if not parent_id:
        return None
    return service.events().get(calendarId="primary", eventId=parent_id).execute()


def _event_payload(event: dict, timezone_name: str, user_id: int, *, parent: dict | None = None) -> dict:
    start, end, all_day = _event_interval(event, timezone_name)
    recurrence_source = parent or event
    recurrence = list(recurrence_source.get("recurrence") or [])
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    managed = is_managed_travel_event(event)
    categories = get_user_categories(user_id)
    category = _category(event, user_id)
    labels = {item["key"]: item["label"] for item in categories}
    payload = {
        "id": str(event.get("id") or ""),
        "title": str(event.get("summary") or "Событие")[:200],
        "location": str(event.get("location") or "")[:500],
        "all_day": all_day,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "start_date": start.date().isoformat() if all_day else None,
        "end_date": (end.date() - timedelta(days=1)).isoformat() if all_day else None,
        "category": category,
        "category_label": labels.get(category, "Без категории"),
        "category_options": [
            {"key": item["key"], "label": item["label"], "color_id": item.get("color_id")}
            for item in categories
        ],
        "recurrence": _recurrence_key(recurrence),
        "recurrence_raw": recurrence,
        "recurring": bool(event.get("recurringEventId") or recurrence),
        "recurring_instance": bool(event.get("recurringEventId")),
        "reminder": _reminder_key(event),
        "attendees": _attendee_emails(event),
        "timezone": timezone_name,
        "managed": managed,
        "managed_type": private.get("smartPlannerType"),
        "editable": not managed,
    }
    if managed:
        payload["managed_note"] = "Эта дорога управляется навигацией. Измени исходное событие, и трансфер пересчитается автоматически."
    return payload


def _parse_local_datetime(value: object, timezone_name: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Укажи дату и время")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Некорректная дата или время") from exc
    zone = ZoneInfo(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _desired_interval(payload: dict, timezone_name: str) -> tuple[datetime, datetime, bool, dict, dict]:
    zone = ZoneInfo(timezone_name)
    all_day = bool(payload.get("all_day"))
    if all_day:
        try:
            start_date = date.fromisoformat(str(payload.get("start_date") or ""))
            last_date = date.fromisoformat(str(payload.get("end_date") or payload.get("start_date") or ""))
        except ValueError as exc:
            raise ValueError("Укажи корректные даты") from exc
        if last_date < start_date:
            raise ValueError("Последний день не может быть раньше первого")
        start = datetime.combine(start_date, datetime.min.time(), tzinfo=zone)
        end = datetime.combine(last_date + timedelta(days=1), datetime.min.time(), tzinfo=zone)
        return start, end, True, {"date": start_date.isoformat()}, {"date": (last_date + timedelta(days=1)).isoformat()}

    start = _parse_local_datetime(payload.get("start"), timezone_name)
    end = _parse_local_datetime(payload.get("end"), timezone_name)
    if end <= start:
        raise ValueError("Окончание должно быть позже начала")
    if end - start > timedelta(days=31):
        raise ValueError("Событие получилось слишком длинным")
    return (
        start,
        end,
        False,
        {"dateTime": start.isoformat(), "timeZone": timezone_name},
        {"dateTime": end.isoformat(), "timeZone": timezone_name},
    )


def _series_interval_patch(
    instance: dict,
    parent: dict,
    desired_start: datetime,
    desired_end: datetime,
    desired_all_day: bool,
    timezone_name: str,
) -> tuple[dict, dict]:
    instance_start, instance_end, instance_all_day = _event_interval(instance, timezone_name)
    parent_start, _, parent_all_day = _event_interval(parent, timezone_name)
    if desired_all_day != instance_all_day or desired_all_day != parent_all_day:
        raise ValueError("Для всей серии нельзя менять режим «весь день». Измени только это событие или будущие.")
    delta = desired_start - instance_start
    new_parent_start = parent_start + delta
    duration = desired_end - desired_start
    new_parent_end = new_parent_start + duration
    if desired_all_day:
        return (
            {"date": new_parent_start.date().isoformat()},
            {"date": new_parent_end.date().isoformat()},
        )
    return (
        {"dateTime": new_parent_start.isoformat(), "timeZone": timezone_name},
        {"dateTime": new_parent_end.isoformat(), "timeZone": timezone_name},
    )


def _conflict_payload(conflict: dict, timezone_name: str) -> dict:
    try:
        start, end, all_day = _event_interval(conflict, timezone_name)
        return {
            "id": conflict.get("id"),
            "title": str(conflict.get("summary") or "Событие"),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "all_day": all_day,
        }
    except ValueError:
        return {"id": conflict.get("id"), "title": str(conflict.get("summary") or "Событие")}


def _alternatives(
    user_id: int,
    timezone_name: str,
    start: datetime,
    end: datetime,
    *,
    all_day: bool,
    event: dict | None = None,
) -> list[dict]:
    if all_day:
        return []
    own_ids = {
        str(value)
        for value in (
            (event or {}).get("id"),
            (event or {}).get("recurringEventId"),
        )
        if str(value or "").strip()
    }
    try:
        slots = suggest_alternatives(
            user_id,
            timezone_name,
            start,
            end - start,
            limit=3,
            exclude_event_ids=own_ids,
        )
    except Exception:
        return []
    return [
        {"start": slot_start.isoformat(), "end": slot_end.isoformat()}
        for slot_start, slot_end in slots
    ]


def _is_linked_managed_travel(event: dict, source_ids: set[str]) -> bool:
    if not is_managed_travel_event(event):
        return False
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    return str(private.get("smartPlannerSourceEventId") or "") in source_ids


def _filtered_conflicts(user_id: int, start: datetime, end: datetime, event: dict) -> list[dict]:
    own_ids = {str(event.get("id") or ""), str(event.get("recurringEventId") or "")}
    own_ids.discard("")
    conflicts = _find_conflicts(user_id, start, end, exclude_event_id=event.get("id"))
    return [
        item for item in conflicts
        if str(item.get("id") or "") not in own_ids
        and str(item.get("recurringEventId") or "") not in own_ids
        and not _is_linked_managed_travel(item, own_ids)
    ]


@event_detail_api.get("/api/mobile/events/<event_id>")
def event_details(event_id: str):
    user_id = _user_id()
    timezone_name = _timezone(user_id)
    try:
        service = _get_calendar_service(user_id)
        event = service.events().get(calendarId="primary", eventId=event_id).execute()
        parent = _load_parent(service, event)
        return {"event": _event_payload(event, timezone_name, user_id, parent=parent)}
    except PermissionError:
        return {"error": "calendar_not_connected", "message": "Google Calendar не подключён."}, 409
    except Exception:
        return {"error": "event_not_found", "message": "Не удалось загрузить событие."}, 404


@event_detail_api.patch("/api/mobile/events/<event_id>")
def update_event(event_id: str):
    user_id = _user_id()
    timezone_name = _timezone(user_id)
    payload = request.get_json(silent=True) or {}
    try:
        service = _get_calendar_service(user_id)
        event = service.events().get(calendarId="primary", eventId=event_id).execute()
        if is_managed_travel_event(event):
            return {"error": "managed_travel", "message": "Системный трансфер меняется через исходное событие."}, 409
        parent = _load_parent(service, event)
        recurring_instance = bool(event.get("recurringEventId"))
        scope = str(payload.get("scope") or "this")
        if recurring_instance and scope not in {"this", "future", "series"}:
            raise ValueError("Выбери область изменения повторяющегося события")
        if not recurring_instance:
            scope = "this"

        target = parent if scope == "series" and parent else event
        title = " ".join(str(payload.get("title") or "").split()).strip()
        if not title:
            raise ValueError("Название не может быть пустым")
        if len(title) > 200:
            raise ValueError("Сократи название до 200 символов")
        location = " ".join(str(payload.get("location") or "").split()).strip()[:500]
        category = str(payload.get("category") or "").strip().lower()
        categories = get_user_categories(user_id)
        active_categories = {item["key"] for item in categories}
        if category not in active_categories:
            raise ValueError("Выбери существующую категорию")

        desired_start, desired_end, desired_all_day, start_part, end_part = _desired_interval(payload, timezone_name)
        instance_start, instance_end, instance_all_day = _event_interval(event, timezone_name)
        patch: dict = {
            "summary": title,
            "location": location,
            "description": _description_with_category(target.get("description"), category),
            "colorId": get_category_colors(user_id).get(category),
        }

        interval_changed = (
            desired_start != instance_start
            or desired_end != instance_end
            or desired_all_day != instance_all_day
        )
        if interval_changed:
            if scope == "series" and parent:
                start_part, end_part = _series_interval_patch(
                    event, parent, desired_start, desired_end, desired_all_day, timezone_name
                )
            patch["start"] = start_part
            patch["end"] = end_part

        requested_recurrence = str(payload.get("recurrence") or "none")
        current_recurrence = _recurrence_key(list((parent or event).get("recurrence") or []))
        if requested_recurrence != current_recurrence and requested_recurrence != "custom":
            if recurring_instance and scope == "this":
                raise ValueError("Повтор меняется для будущих событий или всей серии, но не для одного экземпляра")
            if requested_recurrence == "none":
                patch["recurrence"] = []
            elif requested_recurrence in RECURRENCE_RULES:
                patch["recurrence"] = [RECURRENCE_RULES[requested_recurrence]]
            else:
                raise ValueError("Неизвестное правило повтора")

        requested_reminder = str(payload.get("reminder") or "default")
        current_reminder = _reminder_key(target)
        if requested_reminder not in REMINDER_VALUES:
            raise ValueError("Неизвестное напоминание")
        if requested_reminder != current_reminder and requested_reminder != "custom":
            if requested_reminder == "default":
                patch["reminders"] = {"useDefault": True}
            elif requested_reminder == "none":
                patch["reminders"] = {"useDefault": False, "overrides": []}
            else:
                patch["reminders"] = {
                    "useDefault": False,
                    "overrides": [{"method": "popup", "minutes": int(requested_reminder)}],
                }

        raw_attendees = payload.get("attendees") or []
        if not isinstance(raw_attendees, list):
            raise ValueError("Некорректный список участников")
        attendee_emails: list[str] = []
        for raw in raw_attendees:
            email = str(raw or "").strip().lower()
            if not email:
                continue
            if "@" not in email or len(email) > 254:
                raise ValueError(f"Некорректный email: {email[:80]}")
            if email not in attendee_emails:
                attendee_emails.append(email)
        if attendee_emails != _attendee_emails(target):
            patch["attendees"] = [{"email": email} for email in attendee_emails[:20]]

        if interval_changed and not bool(payload.get("allow_conflict")):
            conflicts = _filtered_conflicts(user_id, desired_start, desired_end, event)
            if conflicts:
                return {
                    "error": "calendar_conflict",
                    "message": "В новом времени уже есть другое событие.",
                    "conflict": _conflict_payload(conflicts[0], timezone_name),
                    "alternatives": _alternatives(
                        user_id,
                        timezone_name,
                        desired_start,
                        desired_end,
                        all_day=desired_all_day,
                        event=event,
                    ),
                }, 409

        if scope == "future" and recurring_instance:
            updated = split_recurring_series_for_update(service, event, patch, timezone_name)
        else:
            target_id = str(target.get("id") or event_id)
            kwargs = {"calendarId": "primary", "eventId": target_id, "body": patch}
            if "attendees" in patch:
                kwargs["sendUpdates"] = "all"
            updated = service.events().patch(**kwargs).execute()

        if not is_managed_travel_event(updated):
            try:
                sync_travel_for_event(user_id, updated, timezone_name)
            except Exception:
                pass
        updated_parent = _load_parent(service, updated)
        return {"event": _event_payload(updated, timezone_name, user_id, parent=updated_parent)}
    except ValueError as exc:
        return {"error": "invalid_event", "message": str(exc)}, 400
    except PermissionError:
        return {"error": "calendar_not_connected", "message": "Google Calendar не подключён."}, 409
    except Exception:
        return {"error": "calendar_update_failed", "message": "Не удалось изменить событие в календаре."}, 500


@event_detail_api.delete("/api/mobile/events/<event_id>")
def delete_event(event_id: str):
    user_id = _user_id()
    timezone_name = _timezone(user_id)
    payload = request.get_json(silent=True) or {}
    try:
        service = _get_calendar_service(user_id)
        event = service.events().get(calendarId="primary", eventId=event_id).execute()
        if is_managed_travel_event(event):
            return {"error": "managed_travel", "message": "Системный трансфер управляется навигацией."}, 409
        recurring_instance = bool(event.get("recurringEventId"))
        scope = str(payload.get("scope") or "this")
        if recurring_instance and scope not in {"this", "future", "series"}:
            raise ValueError("Выбери область удаления повторяющегося события")
        if not recurring_instance:
            scope = "this"

        if scope == "series" and event.get("recurringEventId"):
            service.events().delete(calendarId="primary", eventId=event["recurringEventId"]).execute()
        elif scope == "future" and event.get("recurringEventId"):
            trim_recurring_series_from(service, event, timezone_name)
        else:
            service.events().delete(calendarId="primary", eventId=event_id).execute()
            try:
                safe_delete_travel_for_event(user_id, event_id)
            except Exception:
                pass
        return {"ok": True, "scope": scope}
    except ValueError as exc:
        return {"error": "invalid_event", "message": str(exc)}, 400
    except PermissionError:
        return {"error": "calendar_not_connected", "message": "Google Calendar не подключён."}, 409
    except Exception:
        return {"error": "calendar_delete_failed", "message": "Не удалось удалить событие из календаря."}, 500


# Category management shares the same authenticated web app and is registered
# here because this blueprint is already part of the mobile calendar shell.
event_detail_api.register_blueprint(category_api)
