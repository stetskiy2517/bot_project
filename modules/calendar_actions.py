"""Изменяющие действия AI Smart Planner для Google Calendar."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_calendar_preferences, get_category_colors, get_user_timezone
from modules.calendar import (
    NAMED_DATE_RE,
    NUMERIC_DATE_RE,
    _build_event,
    _create_event,
    _date_from_text,
    _extract_time,
    _parse_event_timing,
)
from modules.calendar_availability import create_event_in_slot, format_alternatives, suggest_alternatives
from modules.calendar_event_features import (
    _append_until,
    _extract_attendees,
    _extract_location,
    _priority_value,
    _recurrence_rule,
    _reminder_minutes,
    apply_event_features,
    build_all_day_event,
    is_all_day,
)
from modules.calendar_recurrence import (
    is_recurring_instance,
    recurring_scope_from_text,
    split_recurring_series_for_update,
    trim_recurring_series_from,
)
from modules.calendar_user import (
    _event_start,
    _format_event_line,
    _get_calendar_service,
    _list_events,
    _parse_search_period,
    _user_zone,
)
from modules.navigation import safe_delete_travel_for_event, sync_travel_for_event

logger = logging.getLogger(__name__)

YES_WORDS = {"да", "ага", "подтверждаю", "подтвердить", "создавай", "удаляй", "меняй", "ок", "окей"}
NO_WORDS = {"нет", "не надо", "отмена", "отменить", "стоп"}

DELETE_PREFIX_RE = re.compile(r"^(?:удали|удалить|отмени|отменить|убери|убрать)\s+", re.IGNORECASE)
UPDATE_PREFIX_RE = re.compile(
    r"^(?:перенеси|перенести|сдвинь|сдвинуть|измени|изменить|поменяй|поменять|сделай|переименуй)\s+",
    re.IGNORECASE,
)
PROPERTY_UPDATE_PREFIX_RE = re.compile(
    r"^\s*(?:измени|изменить|поменяй|поменять|добавь|добавить|убери|убрать|удали|удалить|поставь|поставить)\s+"
    r"(?:место|адрес|категори\w*|приоритет\w*|напоминани\w*|повтор\w*|участник\w*)\s*"
    r"(?:(?:у|для|к)\s+)?",
    re.IGNORECASE,
)
DATE_TAIL_RE = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|в\s+понедельник\w*|во?\s+вторник\w*|в\s+сред\w*|"
    r"в\s+четверг\w*|в\s+пятниц\w*|в\s+суббот\w*|в\s+воскресень\w*)\b",
    re.IGNORECASE,
)
SCOPE_PHRASE_RE = re.compile(
    r"\b(?:только\s+(?:эту|это|одну|один)|только\s+(?:сегодня|завтра)|одно\s+событие|эту\s+встречу|"
    r"все\s+будущ\w*|эту\s+и\s+все\s+следующ\w*|это\s+и\s+все\s+следующ\w*|с\s+этой\s+и\s+дальше|"
    r"начиная\s+с\s+этой|все\s+следующ\w*|всю\s+серию|весь\s+цикл|все\s+повторени\w*|"
    r"все\s+события\s+серии|все\s+встречи\s+серии)\b",
    re.IGNORECASE,
)
BULK_DELETE_TARGET_RE = re.compile(
    r"^(?:все|всё|всю|весь)\s*(?:встреч\w*|событ\w*|созвон\w*|звонк\w*|запис\w*|дел\w*)?$",
    re.IGNORECASE,
)
DELETE_WEEK_PERIOD_RE = re.compile(
    r"\b(?:на\s+)?(?:эт\w*|текущ\w*|следующ\w*)\s+недел\w*\b",
    re.IGNORECASE,
)
DURATION_RE = re.compile(
    r"\bна\s+(?:(полчаса)|(полтора\s+часа)|(\d+)\s*(минут\w*|час\w*))\b",
    re.IGNORECASE,
)
RENAME_RE = re.compile(r"^переименуй\s+(.+?)\s+в\s+(.+)$", re.IGNORECASE)
CHOICE_WORD_RE = re.compile(r"\b(перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*)\b", re.IGNORECASE)
FREE_CHOICE_PREFIX_RE = re.compile(
    r"^\s*(?:поставь|поставить|создай|создать|запланируй|запланировать|назначь|назначить|добавь|добавить|займи|занять)\s*",
    re.IGNORECASE,
)
FREE_CHOICE_MARKER_RE = re.compile(
    r"\s*(?:на|в)?\s*(?:(?:перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*)\s+)?(?:вариант\w*|(?:это|этот|это\s+же)?\s*окн\w*)\b.*$",
    re.IGNORECASE,
)
LOCATION_UPDATE_VALUE_RE = re.compile(
    r"\b(?:место|адрес)\b[^\n]{0,80}?\s+на\s+(?P<value>.+?)(?=$|\s+(?:за\s+\d|напомин|приоритет|категор|повтор|участник))",
    re.IGNORECASE,
)
EMAIL_TOKEN_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
CATEGORY_NAMES = {
    "работ": "work",
    "здоров": "health",
    "отдых": "rest",
    "поезд": "travel",
    "путеш": "travel",
    "сем": "family",
    "личн": "personal",
    "проч": "other",
}


def _normalise(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


def _event_end(event: dict, timezone: str) -> datetime | None:
    end = event.get("end") or {}
    zone = _user_zone(timezone)
    if end.get("dateTime"):
        return datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00")).astimezone(zone)
    if end.get("date"):
        return datetime.fromisoformat(end["date"]).replace(tzinfo=zone)
    return None


def _find_conflicts(user_id: int, start: datetime, end: datetime, *, exclude_event_id: str | None = None) -> list[dict]:
    prefs = get_calendar_preferences(user_id)
    buffer = timedelta(minutes=prefs["buffer_minutes"])
    query_start = start - buffer
    query_end = end + buffer
    events = _list_events(user_id, query_start, query_end)
    conflicts = []
    timezone = str(start.tzinfo) if start.tzinfo else "Europe/Moscow"
    for event in events:
        if event.get("transparency") == "transparent":
            continue
        if exclude_event_id and event.get("id") == exclude_event_id:
            continue
        event_start, _ = _event_start(event, timezone)
        event_end = _event_end(event, timezone)
        if event_start and event_end and event_start < query_end and event_end > query_start:
            conflicts.append(event)
    return conflicts


def _query_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[a-zа-я0-9]+", _normalise(query))
    return [token for token in tokens if len(token) >= 3]


def _event_matches_query(event: dict, query: str) -> bool:
    haystack = _normalise(" ".join([
        str(event.get("summary") or ""),
        str(event.get("description") or ""),
        str(event.get("location") or ""),
    ]))
    hay_tokens = re.findall(r"[a-zа-я0-9]+", haystack)
    for token in _query_tokens(query):
        prefix = token[:4] if len(token) >= 4 else token
        if not any(word.startswith(prefix) or prefix in word for word in hay_tokens):
            return False
    return True


def _candidate_search(user_id: int, timezone: str, text: str, query: str, *, use_text_period: bool = True) -> list[dict]:
    zone = _user_zone(timezone)
    if use_text_period:
        start, end = _parse_search_period(text, timezone)
    else:
        start = datetime.now(zone)
        end = start + timedelta(days=365)
    return [event for event in _list_events(user_id, start, end) if _event_matches_query(event, query)]


def _clean_date_tokens(value: str) -> str:
    value = DATE_TAIL_RE.sub(" ", value)
    value = NUMERIC_DATE_RE.sub(" ", value)
    value = NAMED_DATE_RE.sub(" ", value)
    value = SCOPE_PHRASE_RE.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip(" ,.-")


def _extract_delete_query(text: str) -> str:
    query = DELETE_PREFIX_RE.sub("", text.strip().rstrip("?.!,"))
    return _clean_date_tokens(query)


def _is_bulk_delete_request(text: str) -> bool:
    body = DELETE_PREFIX_RE.sub("", text.strip().rstrip("?.!,"))
    body = DELETE_WEEK_PERIOD_RE.sub(" ", body)
    body = _clean_date_tokens(body)
    body = re.sub(r"\s+", " ", body).strip(" ,.-")
    return bool(BULK_DELETE_TARGET_RE.fullmatch(body))


def _has_explicit_delete_period(text: str) -> bool:
    return bool(
        DATE_TAIL_RE.search(text)
        or NUMERIC_DATE_RE.search(text)
        or NAMED_DATE_RE.search(text)
        or DELETE_WEEK_PERIOD_RE.search(text)
    )


def _extract_update_target(text: str) -> str:
    rename = RENAME_RE.match(text.strip())
    if rename:
        return rename.group(1).strip()

    property_match = PROPERTY_UPDATE_PREFIX_RE.match(text)
    if property_match:
        body = text[property_match.end():].strip().rstrip("?.!,")
        cut_points: list[int] = []
        marker = re.search(r"\s+(?:на|за)\s+|\s*:\s*", body, re.IGNORECASE)
        if marker:
            cut_points.append(marker.start())
        email = EMAIL_TOKEN_RE.search(body)
        if email:
            cut_points.append(email.start())
        feature_tail = re.search(
            r"\s+(?:ежедневно|еженедельно|ежемесячно|кажд\w*\s+\w+|"
            r"высок\w*|низк\w*|обычн\w*|средн\w*)\b",
            body,
            re.IGNORECASE,
        )
        if feature_tail:
            cut_points.append(feature_tail.start())
        if cut_points:
            body = body[:min(cut_points)]
        return _clean_date_tokens(body)

    body = UPDATE_PREFIX_RE.sub("", text.strip().rstrip("?.!,"))
    body = SCOPE_PHRASE_RE.sub(" ", body)
    duration = DURATION_RE.search(body)
    if duration:
        body = body[: duration.start()]
    else:
        feature_tail = re.search(
            r"\s+(?:с\s+)?(?:высок\w*|низк\w*|обычн\w*|средн\w*)\s+приоритет\w*|"
            r"\s+(?:ежедневно|еженедельно|ежемесячно|кажд\w*\s+\w+)",
            body, re.IGNORECASE,
        )
        if feature_tail:
            body = body[: feature_tail.start()]
        else:
            move = re.search(
                r"\s+на\s+(?=(?:сегодня|завтра|послезавтра|понедельник|вторник|сред|четверг|пятниц|суббот|воскрес|\d))",
                body, re.IGNORECASE,
            )
            if move:
                body = body[: move.start()]
            else:
                move_time = re.search(r"\s+в\s+(?=\d{1,2}(?::|\.|\s)\d{2}\b)", body, re.IGNORECASE)
                if move_time:
                    body = body[: move_time.start()]
    return _clean_date_tokens(body)


def _duration_from_update(text: str) -> timedelta | None:
    match = DURATION_RE.search(_normalise(text))
    if not match:
        return None
    if match.group(1):
        return timedelta(minutes=30)
    if match.group(2):
        return timedelta(minutes=90)
    amount = int(match.group(3))
    return timedelta(minutes=amount) if match.group(4).startswith("минут") else timedelta(hours=amount)


def _new_title_from_update(text: str) -> str | None:
    match = RENAME_RE.match(text.strip())
    if not match:
        return None
    title = match.group(2).strip(" ,.-")
    return title[:1].upper() + title[1:] if title else None


def _explicit_category(text: str) -> str | None:
    lower = _normalise(text)
    if "категор" not in lower:
        return None
    tail = lower.split("категор", 1)[1]
    for root, category in CATEGORY_NAMES.items():
        if root in tail:
            return category
    return None


def _description_with_category(description: str | None, category: str) -> str:
    lines = [
        line for line in (description or "").splitlines()
        if not line.strip().lower().startswith("ai smart planner category:")
    ]
    lines.append(f"AI Smart Planner category: {category}")
    return "\n".join(line for line in lines if line.strip())


def _location_from_update(text: str) -> str | None:
    direct = _extract_location(text)
    if direct:
        return direct
    match = LOCATION_UPDATE_VALUE_RE.search(text)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group("value")).strip(" ,.;")
    return value[:500] if value else None


def _priority_patch(event: dict, priority: str) -> dict:
    extended = dict(event.get("extendedProperties") or {})
    private = dict(extended.get("private") or {})
    private["smartPlannerPriority"] = priority
    extended["private"] = private
    return extended


def _build_update_patch(
    event: dict,
    text: str,
    timezone: str,
    category_colors: dict[str, str | None] | None = None,
) -> dict:
    old_start, all_day = _event_start(event, timezone)
    old_end = _event_end(event, timezone)
    if not old_start or not old_end:
        return {}

    patch: dict = {}
    lower = _normalise(text)

    new_title = _new_title_from_update(text)
    if new_title:
        patch["summary"] = new_title

    if re.search(r"\b(?:убери|удали|без)\s+напомин", lower):
        patch["reminders"] = {"useDefault": False, "overrides": []}
    else:
        reminders = _reminder_minutes(text)
        if reminders:
            patch["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": minutes} for minutes in reminders],
            }

    if re.search(r"\b(?:убери|удали)\s+(?:место|адрес)\b", lower):
        patch["location"] = ""
    else:
        location = _location_from_update(text)
        if location:
            patch["location"] = location

    attendees = _extract_attendees(text)
    existing_attendees = list(event.get("attendees") or [])
    if re.search(r"\b(?:убери|удали)\s+(?:всех\s+)?участник", lower) and not attendees:
        patch["attendees"] = []
    elif attendees:
        if re.search(r"\b(?:убери|удали)\s+участник", lower):
            remove = {item["email"].lower() for item in attendees}
            patch["attendees"] = [
                item for item in existing_attendees
                if str(item.get("email") or "").lower() not in remove
            ]
        elif re.search(r"\b(?:добавь|пригласи)\b", lower):
            merged = list(existing_attendees)
            seen = {str(item.get("email") or "").lower() for item in merged}
            for item in attendees:
                if item["email"] not in seen:
                    merged.append(item)
                    seen.add(item["email"])
            patch["attendees"] = merged
        else:
            patch["attendees"] = attendees

    if re.search(r"\b(?:убери|удали|отмени)\s+(?:повтор|повторение)|\bбольше\s+не\s+повтор", lower):
        patch["recurrence"] = []
    else:
        recurrence = _recurrence_rule(text)
        if recurrence:
            patch["recurrence"] = [_append_until(recurrence, text, event)]

    priority = _priority_value(text)
    if priority:
        patch["extendedProperties"] = _priority_patch(event, priority)
    elif re.search(r"\b(?:убери|сбрось)\s+приоритет", lower):
        patch["extendedProperties"] = _priority_patch(event, "normal")

    category = _explicit_category(text)
    if category:
        colors = category_colors or {}
        patch["description"] = _description_with_category(event.get("description"), category)
        patch["colorId"] = colors.get(category)

    duration = _duration_from_update(text)
    parsed_time = _extract_time(text)
    now = datetime.now(_user_zone(timezone))
    new_date = _date_from_text(text, now, old_start.hour, old_start.minute)

    if all_day:
        return patch

    new_start = old_start
    if new_date:
        new_start = new_start.replace(year=new_date.year, month=new_date.month, day=new_date.day)
    if parsed_time:
        new_start = new_start.replace(hour=parsed_time[0], minute=parsed_time[1], second=0, microsecond=0)

    if new_start != old_start:
        old_duration = old_end - old_start
        effective_duration = duration or old_duration
        patch["start"] = {"dateTime": new_start.isoformat(), "timeZone": timezone}
        patch["end"] = {"dateTime": (new_start + effective_duration).isoformat(), "timeZone": timezone}
    elif duration:
        patch["end"] = {"dateTime": (old_start + duration).isoformat(), "timeZone": timezone}

    return patch


def _patch_interval(event: dict, patch: dict, timezone: str) -> tuple[datetime, datetime] | None:
    if "start" in patch:
        start = datetime.fromisoformat(patch["start"]["dateTime"])
    else:
        start, all_day = _event_start(event, timezone)
        if not start or all_day:
            return None
    if "end" in patch:
        end = datetime.fromisoformat(patch["end"]["dateTime"])
    else:
        end = _event_end(event, timezone)
        if not end:
            return None
    return start, end


def _store_pending(context: ContextTypes.DEFAULT_TYPE, payload: dict) -> None:
    context.user_data["smart_planner_pending"] = payload


def _format_candidates(events: list[dict], timezone: str) -> str:
    return "\n".join(
        f"{index}. {_format_event_line(event, timezone, include_date=True).lstrip('• ')}"
        for index, event in enumerate(events[:5], start=1)
    )


def _choice_index(text: str) -> int | None:
    stripped = text.strip()
    if stripped.isdigit():
        index = int(stripped) - 1
        return index if index >= 0 else None
    match = CHOICE_WORD_RE.search(text)
    if match:
        token = match.group(1).lower().replace("ё", "е")
        if token.startswith("втор"):
            return 1
        if token.startswith("трет"):
            return 2
        if token.startswith("четверт"):
            return 3
        if token.startswith("пят"):
            return 4
        return 0
    normal = _normalise(text)
    if "это окно" in normal or "этот вариант" in normal or normal in {"займи окно", "займи это"}:
        return 0
    return None


def _free_choice_title(text: str) -> str | None:
    body = FREE_CHOICE_PREFIX_RE.sub("", text, count=1).strip()
    body = FREE_CHOICE_MARKER_RE.sub("", body).strip(" ,.-")
    if not body or body.isdigit() or CHOICE_WORD_RE.fullmatch(body):
        return None
    return body


def _event_at_alternative(event: dict, slot: tuple[datetime, datetime], timezone: str) -> dict:
    start, end = slot
    moved = dict(event)
    moved["start"] = {"dateTime": start.isoformat(), "timeZone": timezone}
    moved["end"] = {"dateTime": end.isoformat(), "timeZone": timezone}
    return moved


def _format_alternative_choices(slots: list[tuple[datetime, datetime]]) -> str:
    return "\n".join(
        f"{index}. {start.strftime('%d.%m %H:%M')}–{end.strftime('%H:%M')}"
        for index, (start, end) in enumerate(slots, start=1)
    )


def _recurring_scope_label(scope: str) -> str:
    return {"this": "только это событие", "future": "это и все будущие", "series": "всю серию"}.get(scope, scope)


async def create_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default=None)
    if not timezone:
        await update.message.reply_text("Сначала выбери часовой пояс для календаря: /timezone")
        return True

    try:
        if is_all_day(text):
            event = build_all_day_event(text, timezone, category_colors=get_category_colors(user_id))
            if not event:
                return False
            _create_event(user_id, event)
            await update.message.reply_text(
                f"Событие «{event['summary']}» добавлено на весь день: {event['start']['date']}"
            )
            return True

        zone = _user_zone(timezone)
        local_now = datetime.now(zone).replace(tzinfo=None)
        timing = _parse_event_timing(text, local_now)
        if not timing:
            return False
        start_naive, end_naive = timing
        start = start_naive.replace(tzinfo=zone) if start_naive.tzinfo is None else start_naive.astimezone(zone)
        end = end_naive.replace(tzinfo=zone) if end_naive.tzinfo is None else end_naive.astimezone(zone)

        event = apply_event_features(_build_event(text, start, end, get_category_colors(user_id)), text)
        event["start"]["timeZone"] = timezone
        event["end"]["timeZone"] = timezone
        conflicts = _find_conflicts(user_id, start, end)
        if conflicts:
            alternatives = suggest_alternatives(user_id, timezone, start, end - start, limit=3)
            _store_pending(context, {
                "type": "confirm_create_conflict",
                "event": event,
                "alternatives": alternatives,
                "timezone": timezone,
            })
            alternatives_text = (
                "\nВарианты:\n" + _format_alternative_choices(alternatives)
                if alternatives else ""
            )
            await update.message.reply_text(
                "В это время уже есть событие:\n"
                f"{_format_event_line(conflicts[0], timezone, include_date=True)}"
                f"{alternatives_text}\n"
                "Выбери номер варианта, ответь «да», чтобы оставить исходное время, или «нет» для отмены."
            )
            return True
        _create_event(user_id, event)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        logger.exception("Calendar event creation failed for user %s", user_id)
        await update.message.reply_text("Не удалось добавить событие в Google Calendar. Попробуй ещё раз.")
        return True

    extras = []
    if event.get("recurrence"):
        extras.append("повторяется")
    if event.get("reminders"):
        extras.append("с напоминанием")
    if event.get("location"):
        extras.append(f"место: {event['location']}")
    if event.get("attendees"):
        extras.append(f"участников: {len(event['attendees'])}")
    priority = ((event.get("extendedProperties") or {}).get("private") or {}).get("smartPlannerPriority")
    if priority and priority != "normal":
        extras.append("высокий приоритет" if priority == "high" else "низкий приоритет")
    suffix = " · " + ", ".join(extras) if extras else ""
    await update.message.reply_text(
        f"Событие «{event['summary']}» добавлено: {start.strftime('%d.%m.%Y %H:%M')}–{end.strftime('%H:%M')} ({timezone}){suffix}"
    )
    return True


async def _prepare_delete_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    event: dict,
    text: str,
    timezone: str,
    scope: str | None = None,
) -> bool:
    if is_recurring_instance(event):
        scope = scope or recurring_scope_from_text(text)
        if not scope:
            _store_pending(context, {
                "type": "select_recurring_delete_scope",
                "event": event,
                "timezone": timezone,
            })
            await update.message.reply_text(
                "Это повторяющееся событие. Что удалить: только это, это и все будущие или всю серию?"
            )
            return True
    else:
        scope = "this"

    _store_pending(context, {
        "type": "confirm_delete",
        "event": event,
        "timezone": timezone,
        "scope": scope,
    })
    suffix = f"\nОбласть: {_recurring_scope_label(scope)}." if is_recurring_instance(event) else ""
    await update.message.reply_text(
        "Удалить это событие?\n" + _format_event_line(event, timezone, include_date=True) + suffix + "\nОтветь «да» или «нет»."
    )
    return True


async def delete_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default=None)
    if not timezone:
        await update.message.reply_text("Сначала выбери часовой пояс для календаря: /timezone")
        return True

    bulk_delete = _is_bulk_delete_request(text)
    if bulk_delete and not _has_explicit_delete_period(text):
        await update.message.reply_text("Укажи день или дату, за которую удалить все события.")
        return True

    query = _extract_delete_query(text)
    if not query and not bulk_delete:
        await update.message.reply_text("Какое событие удалить?")
        return True
    try:
        if bulk_delete:
            start, end = _parse_search_period(text, timezone)
            events = _list_events(user_id, start, end)
        else:
            events = _candidate_search(user_id, timezone, text, query, use_text_period=True)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        logger.exception("Calendar delete search failed for user %s", user_id)
        await update.message.reply_text("Не удалось найти событие для удаления.")
        return True

    if not events:
        if bulk_delete:
            await update.message.reply_text("На указанный период событий нет.")
        else:
            await update.message.reply_text(f"Не нашёл событие «{query}».")
        return True

    if bulk_delete:
        preview = _format_candidates(events, timezone)
        suffix = f"\n…и ещё {len(events) - 5}." if len(events) > 5 else ""
        _store_pending(context, {"type": "confirm_delete_many", "events": events, "timezone": timezone})
        await update.message.reply_text(
            f"Удалить все события за указанный период ({len(events)})?\n"
            f"{preview}{suffix}\nОтветь «да» или «нет»."
        )
        return True

    if len(events) > 1:
        visible = events[:5]
        _store_pending(context, {"type": "select_delete", "events": visible, "timezone": timezone, "text": text})
        await update.message.reply_text("Нашёл несколько событий. Напиши номер нужного:\n" + _format_candidates(visible, timezone))
        return True
    return await _prepare_delete_confirmation(update, context, events[0], text, timezone)


async def update_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default=None)
    if not timezone:
        await update.message.reply_text("Сначала выбери часовой пояс для календаря: /timezone")
        return True
    query = _extract_update_target(text)
    if not query:
        await update.message.reply_text("Какое событие изменить?")
        return True
    try:
        events = _candidate_search(user_id, timezone, text, query, use_text_period=False)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        logger.exception("Calendar update search failed for user %s", user_id)
        await update.message.reply_text("Не удалось найти событие для изменения.")
        return True
    if not events:
        await update.message.reply_text(f"Не нашёл событие «{query}».")
        return True
    if len(events) > 1:
        visible = events[:5]
        _store_pending(context, {"type": "select_update", "events": visible, "timezone": timezone, "text": text})
        await update.message.reply_text("Нашёл несколько событий. Напиши номер нужного:\n" + _format_candidates(visible, timezone))
        return True
    return await _prepare_update_confirmation(update, context, events[0], text, timezone)


async def _prepare_update_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    event: dict,
    text: str,
    timezone: str,
    scope: str | None = None,
) -> bool:
    target_event = event
    if is_recurring_instance(event):
        scope = scope or recurring_scope_from_text(text)
        if not scope:
            _store_pending(context, {
                "type": "select_recurring_update_scope",
                "event": event,
                "timezone": timezone,
                "text": text,
            })
            await update.message.reply_text(
                "Это повторяющееся событие. Что изменить: только это, это и все будущие или всю серию?"
            )
            return True
        if scope == "series":
            try:
                service = _get_calendar_service(update.effective_user.id)
                target_event = service.events().get(
                    calendarId="primary", eventId=event["recurringEventId"]
                ).execute()
            except Exception:
                logger.exception("Failed to load recurring parent for user %s", update.effective_user.id)
                await update.message.reply_text("Не удалось загрузить серию повторяющихся событий.")
                return True
    else:
        scope = "this"

    patch = _build_update_patch(
        target_event,
        text,
        timezone,
        category_colors=get_category_colors(update.effective_user.id),
    )
    if not patch:
        await update.message.reply_text("Не понял, что именно изменить в событии.")
        return True

    conflict_text = ""
    interval = _patch_interval(target_event, patch, timezone)
    if interval and ("start" in patch or "end" in patch):
        conflicts = _find_conflicts(update.effective_user.id, interval[0], interval[1], exclude_event_id=event.get("id"))
        if conflicts:
            alternatives = suggest_alternatives(
                update.effective_user.id,
                timezone,
                interval[0],
                interval[1] - interval[0],
                limit=3,
            )
            conflict_text = (
                f"\nВ новом времени есть конфликт: {_format_event_line(conflicts[0], timezone, include_date=True)}"
                f"\n{format_alternatives(alternatives)}"
            )

    pending_type = "confirm_update_future" if scope == "future" else "confirm_update"
    _store_pending(context, {
        "type": pending_type,
        "event": event if scope == "future" else target_event,
        "patch": patch,
        "timezone": timezone,
        "scope": scope,
    })
    scope_text = f"\nОбласть: {_recurring_scope_label(scope)}." if is_recurring_instance(event) else ""
    await update.message.reply_text(
        "Подтвердить изменение события?\n"
        f"{_format_event_line(event, timezone, include_date=True)}"
        f"{scope_text}{conflict_text}\nОтветь «да» или «нет»."
    )
    return True


async def resume_pending_action(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, pending: dict) -> bool:
    normal = _normalise(text)
    pending_type = pending.get("type")

    if normal in NO_WORDS:
        context.user_data.pop("smart_planner_pending", None)
        await update.message.reply_text("Хорошо, отменил действие.")
        return True

    if pending_type == "free_slot_title":
        title = text.strip(" ,.-")
        if not title:
            await update.message.reply_text("Напиши, что поставить в это время.")
            return True
        slot = pending.get("slot")
        if not slot:
            context.user_data.pop("smart_planner_pending", None)
            return False
        context.user_data.pop("smart_planner_pending", None)
        try:
            event = create_event_in_slot(update.effective_user.id, pending["timezone"], title, slot[0], slot[1])
            await update.message.reply_text(
                f"Поставил «{event['summary']}» на {slot[0].strftime('%d.%m %H:%M')}–{slot[1].strftime('%H:%M')}."
            )
        except Exception:
            logger.exception("Free slot booking failed for user %s", update.effective_user.id)
            await update.message.reply_text("Не удалось создать событие в выбранном окне.")
        return True

    if pending_type == "free_slot_choice":
        index = _choice_index(text)
        if index is None:
            context.user_data.pop("smart_planner_pending", None)
            return False
        slots = pending.get("slots") or []
        if index < 0 or index >= len(slots):
            await update.message.reply_text("Такого варианта нет. Выбери номер из списка.")
            return True
        slot = slots[index]
        title = _free_choice_title(text)
        if title:
            context.user_data.pop("smart_planner_pending", None)
            try:
                event = create_event_in_slot(update.effective_user.id, pending["timezone"], title, slot[0], slot[1])
                await update.message.reply_text(
                    f"Поставил «{event['summary']}» на {slot[0].strftime('%d.%m %H:%M')}–{slot[1].strftime('%H:%M')}."
                )
            except Exception:
                logger.exception("Free slot booking failed for user %s", update.effective_user.id)
                await update.message.reply_text("Не удалось создать событие в выбранном окне.")
            return True
        _store_pending(context, {"type": "free_slot_title", "slot": slot, "timezone": pending["timezone"]})
        await update.message.reply_text(
            f"Выбрал {slot[0].strftime('%d.%m %H:%M')}–{slot[1].strftime('%H:%M')}. Что поставить в это время?"
        )
        return True

    if pending_type == "confirm_create_conflict":
        index = _choice_index(text)
        alternatives = pending.get("alternatives") or []
        if index is not None:
            if index < 0 or index >= len(alternatives):
                await update.message.reply_text("Такого варианта нет. Выбери номер из списка, «да» или «нет».")
                return True
            context.user_data.pop("smart_planner_pending", None)
            moved = _event_at_alternative(pending["event"], alternatives[index], pending.get("timezone") or "Europe/Moscow")
            try:
                _create_event(update.effective_user.id, moved)
                slot = alternatives[index]
                await update.message.reply_text(
                    f"Поставил «{moved.get('summary', 'Событие')}» на {slot[0].strftime('%d.%m %H:%M')}–{slot[1].strftime('%H:%M')}."
                )
            except Exception:
                logger.exception("Conflict alternative creation failed for user %s", update.effective_user.id)
                await update.message.reply_text("Не удалось создать событие в выбранное время.")
            return True

    if pending_type == "select_recurring_delete_scope":
        scope = recurring_scope_from_text(text)
        if not scope:
            await update.message.reply_text("Ответь: «только эту», «все будущие» или «всю серию».")
            return True
        return await _prepare_delete_confirmation(
            update, context, pending["event"], text, pending["timezone"], scope=scope
        )

    if pending_type == "select_recurring_update_scope":
        scope = recurring_scope_from_text(text)
        if not scope:
            await update.message.reply_text("Ответь: «только эту», «все будущие» или «всю серию».")
            return True
        return await _prepare_update_confirmation(
            update, context, pending["event"], pending["text"], pending["timezone"], scope=scope
        )

    if pending_type in {"select_delete", "select_update"}:
        if not text.strip().isdigit():
            await update.message.reply_text("Напиши номер события из списка или «отмена».")
            return True
        index = int(text.strip()) - 1
        events = pending.get("events") or []
        if index < 0 or index >= len(events):
            await update.message.reply_text("Такого номера нет. Выбери номер из списка.")
            return True
        event = events[index]
        timezone = pending["timezone"]
        if pending_type == "select_delete":
            return await _prepare_delete_confirmation(update, context, event, pending.get("text") or "", timezone)
        return await _prepare_update_confirmation(update, context, event, pending["text"], timezone)

    if normal not in YES_WORDS:
        await update.message.reply_text("Ответь «да» или «нет».")
        return True

    context.user_data.pop("smart_planner_pending", None)
    user_id = update.effective_user.id
    try:
        if pending_type == "confirm_create_conflict":
            _create_event(user_id, pending["event"])
            await update.message.reply_text(f"Событие «{pending['event'].get('summary', 'Без названия')}» добавлено несмотря на конфликт.")
            return True
        if pending_type == "confirm_delete":
            service = _get_calendar_service(user_id)
            event = pending["event"]
            scope = pending.get("scope") or "this"
            if scope == "series" and event.get("recurringEventId"):
                service.events().delete(calendarId="primary", eventId=event["recurringEventId"]).execute()
            elif scope == "future" and event.get("recurringEventId"):
                trim_recurring_series_from(service, event, pending["timezone"])
            else:
                service.events().delete(calendarId="primary", eventId=event["id"]).execute()
                safe_delete_travel_for_event(user_id, event["id"])
            await update.message.reply_text(f"Событие «{event.get('summary', 'Без названия')}» удалено.")
            return True
        if pending_type == "confirm_delete_many":
            service = _get_calendar_service(user_id)
            events = pending.get("events") or []
            deleted = 0
            failed = 0
            for event in events:
                event_id = event.get("id")
                if not event_id:
                    failed += 1
                    continue
                try:
                    service.events().delete(calendarId="primary", eventId=event_id).execute()
                    deleted += 1
                except Exception:
                    failed += 1
                    logger.exception("Failed to bulk-delete calendar event %s for user %s", event_id, user_id)
            if failed:
                await update.message.reply_text(
                    f"Удалено событий: {deleted}. Не удалось удалить: {failed}."
                )
            else:
                await update.message.reply_text(f"Удалил все события за указанный период: {deleted}.")
            return True
        if pending_type == "confirm_update":
            service = _get_calendar_service(user_id)
            patch_kwargs = {
                "calendarId": "primary",
                "eventId": pending["event"]["id"],
                "body": pending["patch"],
            }
            if "attendees" in pending["patch"]:
                patch_kwargs["sendUpdates"] = "all"
            updated = service.events().patch(**patch_kwargs).execute()
            sync_travel_for_event(user_id, updated, pending["timezone"])
            await update.message.reply_text(
                f"Событие «{updated.get('summary', pending['event'].get('summary', 'Без названия'))}» изменено."
            )
            return True
        if pending_type == "confirm_update_future":
            service = _get_calendar_service(user_id)
            updated = split_recurring_series_for_update(
                service,
                pending["event"],
                pending["patch"],
                pending["timezone"],
            )
            await update.message.reply_text(
                f"Событие «{updated.get('summary', pending['event'].get('summary', 'Без названия'))}» изменено с этого момента и дальше."
            )
            return True
    except ValueError as exc:
        logger.warning("Calendar action rejected for user %s: %s", user_id, exc)
        await update.message.reply_text("Не удалось безопасно изменить эту серию. Попробуй изменить только это событие или всю серию.")
        return True
    except Exception:
        logger.exception("Calendar pending action failed for user %s", user_id)
        await update.message.reply_text("Не удалось выполнить действие в Google Calendar.")
        return True

    return False