"""Управление отдельными экземплярами и сериями повторяющихся событий."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone as dt_timezone
import logging
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

THIS_SCOPE_RE = re.compile(
    r"\b(?:только\s+(?:эту|это|одну|один)|только\s+(?:сегодня|завтра)|одно\s+событие|эту\s+встречу)\b",
    re.IGNORECASE,
)
FUTURE_SCOPE_RE = re.compile(
    r"\b(?:все\s+будущ\w*|эту\s+и\s+все\s+следующ\w*|это\s+и\s+все\s+следующ\w*|"
    r"с\s+этой\s+и\s+дальше|начиная\s+с\s+этой|все\s+следующ\w*)\b",
    re.IGNORECASE,
)
SERIES_SCOPE_RE = re.compile(
    r"\b(?:всю\s+серию|весь\s+цикл|все\s+повторени\w*|все\s+события\s+серии|все\s+встречи\s+серии)\b",
    re.IGNORECASE,
)


def recurring_scope_from_text(text: str) -> str | None:
    if FUTURE_SCOPE_RE.search(text):
        return "future"
    if SERIES_SCOPE_RE.search(text):
        return "series"
    if THIS_SCOPE_RE.search(text):
        return "this"
    return None


def is_recurring_instance(event: dict) -> bool:
    return bool(event.get("recurringEventId"))


def _zone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return dt_timezone.utc


def _event_datetime(part: dict | None, timezone_name: str) -> datetime | None:
    part = part or {}
    if part.get("dateTime"):
        return datetime.fromisoformat(part["dateTime"].replace("Z", "+00:00")).astimezone(_zone(timezone_name))
    if part.get("date"):
        return datetime.fromisoformat(part["date"]).replace(tzinfo=_zone(timezone_name))
    return None


def _original_start(event: dict, timezone_name: str) -> tuple[datetime | None, bool]:
    part = event.get("originalStartTime") or event.get("start") or {}
    if part.get("dateTime"):
        return _event_datetime(part, timezone_name), False
    if part.get("date"):
        return _event_datetime(part, timezone_name), True
    return None, False


def _until_before(event: dict, timezone_name: str) -> tuple[str, bool]:
    target, all_day = _original_start(event, timezone_name)
    if not target:
        raise ValueError("Recurring instance has no original start")
    if all_day:
        return (target.date() - timedelta(days=1)).strftime("%Y%m%d"), True
    before = (target - timedelta(seconds=1)).astimezone(dt_timezone.utc)
    return before.strftime("%Y%m%dT%H%M%SZ"), False


def _trim_rrule(rule: str, until: str) -> str:
    parts = rule.split(";")
    kept = [part for part in parts if not part.upper().startswith(("UNTIL=", "COUNT="))]
    kept.append(f"UNTIL={until}")
    return ";".join(kept)


def _trim_recurrence(recurrence: list[str], until: str) -> list[str]:
    result = []
    changed = False
    for rule in recurrence:
        if rule.upper().startswith("RRULE:"):
            result.append(_trim_rrule(rule, until))
            changed = True
        else:
            result.append(rule)
    if not changed:
        raise ValueError("Recurring parent has no RRULE")
    return result


def _parent_start(parent: dict, timezone_name: str) -> datetime | None:
    return _event_datetime(parent.get("start"), timezone_name)


def trim_recurring_series_from(service, instance: dict, timezone_name: str) -> dict | None:
    """Удалить выбранный экземпляр и все будущие, обрезав RRULE родителя."""
    parent_id = instance.get("recurringEventId")
    if not parent_id:
        raise ValueError("Not a recurring instance")
    parent = service.events().get(calendarId="primary", eventId=parent_id).execute()
    target, _ = _original_start(instance, timezone_name)
    parent_start = _parent_start(parent, timezone_name)
    if target and parent_start and target <= parent_start:
        service.events().delete(calendarId="primary", eventId=parent_id).execute()
        return None
    until, _ = _until_before(instance, timezone_name)
    recurrence = parent.get("recurrence") or []
    return service.events().patch(
        calendarId="primary",
        eventId=parent_id,
        body={"recurrence": _trim_recurrence(recurrence, until)},
    ).execute()


def _copy_insertable_parent(parent: dict) -> dict:
    allowed = {
        "summary", "description", "location", "start", "end", "recurrence", "attendees",
        "reminders", "colorId", "transparency", "visibility", "extendedProperties",
        "guestsCanInviteOthers", "guestsCanModify", "guestsCanSeeOtherGuests",
    }
    return {key: deepcopy(value) for key, value in parent.items() if key in allowed}


def _has_count_rule(recurrence: list[str]) -> bool:
    return any(rule.upper().startswith("RRULE:") and ";COUNT=" in rule.upper() for rule in recurrence)


def split_recurring_series_for_update(
    service,
    instance: dict,
    patch: dict,
    timezone_name: str,
) -> dict:
    """Применить изменение к выбранному и всем будущим экземплярам через разделение серии.

    Google рекомендует именно разделять серию, а не создавать множество исключений.
    Серии с COUNT не делим автоматически: для них надо пересчитать оставшееся число повторений.
    """
    parent_id = instance.get("recurringEventId")
    if not parent_id:
        raise ValueError("Not a recurring instance")
    parent = service.events().get(calendarId="primary", eventId=parent_id).execute()
    recurrence = deepcopy(parent.get("recurrence") or [])
    if not recurrence:
        raise ValueError("Recurring parent has no recurrence")
    if _has_count_rule(recurrence):
        raise ValueError("COUNT recurrence requires manual split")

    new_event = _copy_insertable_parent(parent)
    new_event["start"] = deepcopy(patch.get("start") or instance.get("start"))
    new_event["end"] = deepcopy(patch.get("end") or instance.get("end"))
    if not new_event.get("start") or not new_event.get("end"):
        raise ValueError("Recurring instance has no interval")
    if new_event["start"].get("dateTime"):
        new_event["start"].setdefault("timeZone", timezone_name)
    if new_event["end"].get("dateTime"):
        new_event["end"].setdefault("timeZone", timezone_name)

    for key, value in patch.items():
        if key in {"start", "end"}:
            continue
        new_event[key] = deepcopy(value)
    if "recurrence" not in patch:
        new_event["recurrence"] = recurrence

    insert_kwargs = {"calendarId": "primary", "body": new_event}
    if new_event.get("attendees"):
        insert_kwargs["sendUpdates"] = "all"
    inserted = service.events().insert(**insert_kwargs).execute()
    try:
        target, _ = _original_start(instance, timezone_name)
        parent_start = _parent_start(parent, timezone_name)
        if target and parent_start and target <= parent_start:
            service.events().delete(calendarId="primary", eventId=parent_id).execute()
        else:
            until, _ = _until_before(instance, timezone_name)
            service.events().patch(
                calendarId="primary",
                eventId=parent_id,
                body={"recurrence": _trim_recurrence(recurrence, until)},
            ).execute()
    except Exception:
        inserted_id = inserted.get("id")
        if inserted_id:
            try:
                service.events().delete(calendarId="primary", eventId=inserted_id).execute()
            except Exception:
                logger.exception("Failed to roll back the newly created recurring series")
        raise
    return inserted
