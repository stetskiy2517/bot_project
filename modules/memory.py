"""Background AI memory extraction and calendar observation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import re
import threading
from typing import Any

from core.ai_memory_store import record_ai_memory_event
from core.ai_prompts import memory_system_prompt
from core.db import conn, db_lock
from core.memory_store import (
    MEMORY_KINDS,
    claim_memory_events,
    complete_memory_event,
    fail_memory_event,
    upsert_memory,
)
from integrations.ai import AIError, AIProviderError, complete, complete_structured, is_ai_available
from modules.calendar_user import _list_events

logger = logging.getLogger(__name__)

MEMORY_SCHEMA = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["fact", "preference", "habit", "relationship", "goal", "observation"],
                    },
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "string"},
                    "action_title": {"type": "string"},
                    "action_type": {"type": "string", "enum": ["reminder", "calendar_event"]},
                    "action_confidence": {"type": "number"},
                    "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 480},
                    "schedule": {
                        "type": "object",
                        "properties": {
                            "repeat": {
                                "type": "string",
                                "enum": ["daily", "weekdays", "weekends", "weekly"],
                            },
                            "time": {"type": "string"},
                            "weekday": {"type": "integer", "minimum": 0, "maximum": 6},
                        },
                        "required": ["repeat", "time"],
                        "additionalProperties": False,
                    },
                },
                "required": ["kind", "key", "value", "confidence", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["memories"],
    "additionalProperties": False,
}

MEMORY_SYSTEM_PROMPT = memory_system_prompt()
HABIT_SCHEDULE_TYPES = {"daily", "weekdays", "weekends", "weekly"}
HABIT_ACTION_TYPES = {"reminder", "calendar_event"}
HABIT_CLOCK_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
MIN_ACTION_CONFIDENCE = 0.90

_worker_lock = threading.Lock()
_worker_started = False
_structured_output_disabled = False


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def memory_worker_enabled() -> bool:
    return _env_bool("AI_MEMORY_ENABLED", True) and _env_bool("AI_MEMORY_WORKER_ENABLED", True)


def _stable_calendar_entity_id(google_event_id: str) -> int:
    digest = hashlib.sha256(google_event_id.encode("utf-8")).digest()[:8]
    value = int.from_bytes(digest, "big") & ((1 << 63) - 1)
    return value or 1


def _clean_string(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _compact_time(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("dateTime", "date", "timeZone"):
        if value.get(key):
            result[key] = _clean_string(value[key], 120)
    return result


def _compact_calendar_event(event: dict) -> dict:
    event_id = _clean_string(event.get("id"), 300)
    snapshot = {
        "google_event_id": event_id,
        "summary": _clean_string(event.get("summary") or "Без названия", 500),
        "description": _clean_string(event.get("description"), 1500),
        "location": _clean_string(event.get("location"), 500),
        "start": _compact_time(event.get("start")),
        "end": _compact_time(event.get("end")),
        "recurring_event_id": _clean_string(event.get("recurringEventId"), 300),
        "color_id": _clean_string(event.get("colorId"), 20),
    }
    return snapshot


def _calendar_fingerprint(snapshot: dict) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record_calendar_snapshot(user_id: int, snapshot: dict) -> bool:
    google_event_id = str(snapshot.get("google_event_id") or "").strip()
    if not google_event_id:
        return False
    fingerprint = _calendar_fingerprint(snapshot)
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        try:
            row = conn.execute(
                "SELECT fingerprint FROM ai_calendar_sync WHERE user_id=? AND google_event_id=?",
                (int(user_id), google_event_id),
            ).fetchone()
            if row and row[0] == fingerprint:
                conn.execute(
                    "UPDATE ai_calendar_sync SET last_seen_at=? WHERE user_id=? AND google_event_id=?",
                    (now, int(user_id), google_event_id),
                )
                conn.commit()
                return False
            event_type = "updated" if row else "created"
            conn.execute(
                "INSERT INTO ai_calendar_sync (user_id,google_event_id,fingerprint,last_seen_at) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id,google_event_id) DO UPDATE SET "
                "fingerprint=excluded.fingerprint,last_seen_at=excluded.last_seen_at",
                (int(user_id), google_event_id, fingerprint, now),
            )
            record_ai_memory_event(
                user_id,
                "calendar_event",
                _stable_calendar_entity_id(google_event_id),
                event_type,
                snapshot,
                commit=False,
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise


def sync_calendar_memory_events(*, max_events_per_user: int = 300) -> int:
    if not is_ai_available():
        return 0
    with db_lock:
        user_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT user_id FROM users WHERE google_token IS NOT NULL ORDER BY user_id LIMIT 100"
            ).fetchall()
        ]
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=_env_int("AI_MEMORY_CALENDAR_LOOKBACK_DAYS", 7, 0, 90))
    end = now + timedelta(days=_env_int("AI_MEMORY_CALENDAR_LOOKAHEAD_DAYS", 30, 1, 365))
    recorded = 0
    for user_id in user_ids:
        try:
            events = _list_events(user_id, start, end)
        except Exception:
            logger.exception("AI memory calendar sync failed for user %s", user_id)
            continue
        for event in events[: max(1, min(int(max_events_per_user), 1000))]:
            try:
                if _record_calendar_snapshot(user_id, _compact_calendar_event(event)):
                    recorded += 1
            except Exception:
                logger.exception("AI memory could not journal calendar event for user %s", user_id)
    return recorded


def backfill_memory_events(*, limit: int = 60) -> int:
    """Journal active notes/reminders created before the memory journal existed."""
    safe_limit = max(1, min(int(limit), 200))
    created = 0
    with db_lock:
        try:
            notes = conn.execute(
                "SELECT n.user_id,n.note_id,n.title,n.text,n.created_at,n.updated_at,n.deleted_at "
                "FROM notes n WHERE n.deleted_at IS NULL AND NOT EXISTS ("
                "SELECT 1 FROM ai_memory_events e WHERE e.user_id=n.user_id "
                "AND e.entity_type='note' AND e.entity_id=n.note_id) "
                "ORDER BY n.note_id LIMIT ?",
                (safe_limit,),
            ).fetchall()
            for row in notes:
                snapshot = {
                    "note_id": int(row[1]),
                    "user_id": int(row[0]),
                    "title": row[2],
                    "text": row[3],
                    "created_at": row[4],
                    "updated_at": row[5],
                    "deleted_at": row[6],
                }
                record_ai_memory_event(row[0], "note", row[1], "created", snapshot, commit=False)
                created += 1

            remaining = max(0, safe_limit - len(notes))
            if remaining:
                reminders = conn.execute(
                    "SELECT r.user_id,r.reminder_id,r.text,r.remind_at,r.status,r.created_at,"
                    "r.delivered_at,r.completed_at,r.deleted_at,r.repeat_rule,r.repeat_timezone,r.next_remind_at "
                    "FROM reminders r WHERE r.deleted_at IS NULL AND NOT EXISTS ("
                    "SELECT 1 FROM ai_memory_events e WHERE e.user_id=r.user_id "
                    "AND e.entity_type='reminder' AND e.entity_id=r.reminder_id) "
                    "ORDER BY r.reminder_id LIMIT ?",
                    (remaining,),
                ).fetchall()
                for row in reminders:
                    snapshot = {
                        "user_id": int(row[0]),
                        "reminder_id": int(row[1]),
                        "text": row[2],
                        "remind_at": row[3],
                        "status": row[4],
                        "created_at": row[5],
                        "delivered_at": row[6],
                        "completed_at": row[7],
                        "deleted_at": row[8],
                        "repeat_rule": row[9],
                        "repeat_timezone": row[10],
                        "next_remind_at": row[11],
                    }
                    record_ai_memory_event(row[0], "reminder", row[1], "created", snapshot, commit=False)
                    created += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return created


def _similar_calendar_count(user_id: int, snapshot: dict) -> int:
    summary = _clean_string(snapshot.get("summary"), 500).casefold().replace("ё", "е")
    summary = re.sub(r"\s+", " ", summary).strip()
    if not summary:
        return 1
    with db_lock:
        rows = conn.execute(
            "SELECT snapshot_json FROM ai_memory_events "
            "WHERE user_id=? AND entity_type='calendar_event' ORDER BY event_id DESC LIMIT 300",
            (int(user_id),),
        ).fetchall()
    event_ids = set()
    for row in rows:
        try:
            item = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            continue
        candidate = _clean_string(item.get("summary"), 500).casefold().replace("ё", "е")
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate == summary:
            event_ids.add(str(item.get("google_event_id") or ""))
    return max(1, len(event_ids))


def _source_payload(event: dict) -> dict | None:
    entity_type = event["entity_type"]
    snapshot = event.get("snapshot") or {}
    if entity_type == "note":
        text = _clean_string(snapshot.get("text"), 5000)
        if not text:
            return None
        return {
            "source": "note",
            "title": _clean_string(snapshot.get("title"), 200),
            "text": text,
        }
    if entity_type == "reminder":
        text = _clean_string(snapshot.get("text"), 2000)
        if not text:
            return None
        return {
            "source": "reminder",
            "text": text,
            "remind_at": snapshot.get("remind_at"),
            "repeat_rule": snapshot.get("repeat_rule"),
            "repeat_timezone": snapshot.get("repeat_timezone"),
        }
    if entity_type == "calendar_event":
        summary = _clean_string(snapshot.get("summary"), 500)
        if not summary:
            return None
        return {
            "source": "calendar_event",
            "summary": summary,
            "description": _clean_string(snapshot.get("description"), 1500),
            "location": _clean_string(snapshot.get("location"), 500),
            "start": snapshot.get("start") or {},
            "end": snapshot.get("end") or {},
            "recurring_event_id": snapshot.get("recurring_event_id"),
            "similar_events_in_observed_window": _similar_calendar_count(event["user_id"], snapshot),
        }
    if entity_type == "voice_transcript":
        text = _clean_string(snapshot.get("text") or snapshot.get("transcript"), 5000)
        return {"source": "voice_transcript", "text": text} if text else None
    return None


def _parse_json_object(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI memory response does not contain JSON")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("AI memory response must be an object")
    return payload


def _normalize_habit_schedule(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    repeat = str(value.get("repeat") or "").strip().lower()
    clock = str(value.get("time") or "").strip()
    if repeat not in HABIT_SCHEDULE_TYPES or not HABIT_CLOCK_RE.fullmatch(clock):
        return None
    result: dict[str, Any] = {"repeat": repeat, "time": clock}
    if repeat == "weekly":
        try:
            weekday = int(value.get("weekday"))
        except (TypeError, ValueError):
            return None
        if not 0 <= weekday <= 6:
            return None
        result["weekday"] = weekday
    return result


def _habit_value(item: dict, statement: str) -> dict:
    result: dict[str, Any] = {"statement": statement}
    action_title = _clean_string(item.get("action_title"), 160).strip(" .,:;-")
    action_type = str(item.get("action_type") or "").strip().lower()
    schedule = _normalize_habit_schedule(item.get("schedule"))
    try:
        action_confidence = float(item.get("action_confidence"))
    except (TypeError, ValueError):
        return result
    action_confidence = max(0.0, min(1.0, action_confidence))
    if (
        not action_title
        or action_type not in HABIT_ACTION_TYPES
        or schedule is None
        or action_confidence < MIN_ACTION_CONFIDENCE
    ):
        return result
    result.update(
        {
            "action_title": action_title,
            "action_type": action_type,
            "action_confidence": action_confidence,
            "schedule": schedule,
        }
    )
    if action_type == "calendar_event":
        raw_duration = item.get("duration_minutes", 60)
        try:
            duration = int(raw_duration)
        except (TypeError, ValueError):
            return {"statement": statement}
        if not 15 <= duration <= 480:
            return {"statement": statement}
        result["duration_minutes"] = duration
    return result


def _extract_memories(payload: dict) -> list[dict]:
    items = payload.get("memories")
    if not isinstance(items, list):
        raise ValueError("AI memory response has no memories array")
    result = []
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        key = _clean_string(item.get("key"), 120)
        statement = _clean_string(item.get("value"), 1000)
        evidence = _clean_string(item.get("evidence"), 500)
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            continue
        confidence = max(0.0, min(1.0, confidence))
        if kind not in MEMORY_KINDS or not key or not statement or confidence < 0.55:
            continue
        value: Any = _habit_value(item, statement) if kind == "habit" else statement
        result.append(
            {
                "kind": kind,
                "key": key,
                "value": value,
                "confidence": confidence,
                "evidence": evidence,
            }
        )
    return result


def _call_memory_model(source: dict) -> list[dict]:
    global _structured_output_disabled
    source_json = json.dumps(source, ensure_ascii=False, separators=(",", ":"), default=str)
    messages = [
        {"role": "system", "content": MEMORY_SYSTEM_PROMPT},
        {"role": "user", "content": f"Источник для анализа:\n{source_json}"},
    ]
    if not _structured_output_disabled:
        try:
            return _extract_memories(complete_structured(messages, MEMORY_SCHEMA, max_tokens=900))
        except AIProviderError as exc:
            message = str(exc)
            if "HTTP 400" not in message and "HTTP 422" not in message:
                raise
            _structured_output_disabled = True
            logger.warning("Structured AI output unavailable; using validated JSON fallback")
    fallback_messages = [
        {"role": "system", "content": memory_system_prompt(json_only=True)},
        {"role": "user", "content": f"Источник для анализа:\n{source_json}"},
    ]
    raw = complete(fallback_messages, max_tokens=900, temperature=0.001)
    return _extract_memories(_parse_json_object(raw))


def process_memory_event(event: dict) -> int:
    if event.get("event_type") in {"deleted", "delivered", "completed", "reopened"}:
        complete_memory_event(event["event_id"])
        return 0
    source = _source_payload(event)
    if not source:
        complete_memory_event(event["event_id"])
        return 0
    memories = _call_memory_model(source)
    saved = 0
    for item in memories:
        upsert_memory(
            event["user_id"],
            item["kind"],
            item["key"],
            item["value"],
            item["confidence"],
            source_type=event["entity_type"],
            source_id=(event.get("snapshot") or {}).get("google_event_id") or event["entity_id"],
            evidence=item["evidence"],
        )
        saved += 1
    complete_memory_event(event["event_id"])
    return saved


def process_pending_memory_events(*, limit: int = 3) -> dict:
    if not is_ai_available():
        return {"processed": 0, "saved": 0, "failed": 0}
    processed = saved = failed = 0
    for event in claim_memory_events(limit=limit):
        try:
            saved += process_memory_event(event)
            processed += 1
        except AIError as exc:
            failed += 1
            fail_memory_event(event["event_id"], str(exc), attempts=event["attempts"])
            logger.warning("AI memory provider failed for event %s: %s", event["event_id"], exc)
        except Exception as exc:
            failed += 1
            fail_memory_event(event["event_id"], str(exc), attempts=event["attempts"])
            logger.exception("AI memory processing failed for event %s", event["event_id"])
    return {"processed": processed, "saved": saved, "failed": failed}


def _memory_worker_loop() -> None:
    interval = _env_int("AI_MEMORY_WORKER_INTERVAL_SECONDS", 15, 5, 300)
    calendar_interval = _env_int("AI_MEMORY_CALENDAR_SYNC_INTERVAL_SECONDS", 600, 60, 86400)
    next_calendar_sync = 0.0
    import time

    while True:
        try:
            if is_ai_available():
                backfill_memory_events(limit=60)
                now_monotonic = time.monotonic()
                if now_monotonic >= next_calendar_sync:
                    sync_calendar_memory_events()
                    next_calendar_sync = now_monotonic + calendar_interval
                process_pending_memory_events(limit=3)
        except Exception:
            logger.exception("AI memory worker iteration failed")
        time.sleep(interval)


def start_memory_worker() -> None:
    global _worker_started
    if not memory_worker_enabled():
        logger.info("AI memory worker disabled")
        return
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        thread = threading.Thread(target=_memory_worker_loop, name="ai-memory-worker", daemon=True)
        thread.start()
        logger.info("AI memory worker started")
