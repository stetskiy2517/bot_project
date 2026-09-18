"""Ephemeral AI analysis of supported email attachments.

Attachments are fetched into memory, passed through the same file-ingest pipeline as
manual uploads, and never persisted by this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import re

from core.email_store import get_email_account
from integrations.email_gmail import fetch_attachment_bytes as fetch_gmail_attachment_bytes
from integrations.email_imap import extract_attachment_bytes as extract_imap_attachment_bytes
from modules.file_ingest import analyze_file_bytes

logger = logging.getLogger(__name__)

# Attachment analysis is only invoked for an explicit interactive mail review.
# Keep a safety bound, but make it high enough to process normal multi-attachment
# messages completely instead of silently stopping after the first few files.
MAX_ATTACHMENTS_ANALYZED = 12
MAX_ATTACHMENT_ACTIONS = 12
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

SUPPORTED_TYPES = {
    ".pdf": ("application/pdf", MAX_DOCUMENT_BYTES),
    ".txt": ("text/plain", MAX_DOCUMENT_BYTES),
    ".doc": ("application/msword", MAX_DOCUMENT_BYTES),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", MAX_DOCUMENT_BYTES),
    ".epub": ("application/epub", MAX_DOCUMENT_BYTES),
    ".ppt": ("application/ppt", MAX_DOCUMENT_BYTES),
    ".pptx": ("application/pptx", MAX_DOCUMENT_BYTES),
    ".xlsx": ("application/vnd.ms-excel", MAX_DOCUMENT_BYTES),
    ".jpg": ("image/jpeg", MAX_IMAGE_BYTES),
    ".jpeg": ("image/jpeg", MAX_IMAGE_BYTES),
    ".png": ("image/png", MAX_IMAGE_BYTES),
    ".tif": ("image/tiff", MAX_IMAGE_BYTES),
    ".tiff": ("image/tiff", MAX_IMAGE_BYTES),
    ".bmp": ("image/bmp", MAX_IMAGE_BYTES),
}

SUPPORTED_MIME_TYPES = {
    "application/pdf": (".pdf", "application/pdf", MAX_DOCUMENT_BYTES),
    "text/plain": (".txt", "text/plain", MAX_DOCUMENT_BYTES),
    "application/msword": (".doc", "application/msword", MAX_DOCUMENT_BYTES),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        MAX_DOCUMENT_BYTES,
    ),
    "application/epub": (".epub", "application/epub", MAX_DOCUMENT_BYTES),
    "application/epub+zip": (".epub", "application/epub", MAX_DOCUMENT_BYTES),
    "application/ppt": (".ppt", "application/ppt", MAX_DOCUMENT_BYTES),
    "application/vnd.ms-powerpoint": (".ppt", "application/ppt", MAX_DOCUMENT_BYTES),
    "application/pptx": (".pptx", "application/pptx", MAX_DOCUMENT_BYTES),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        ".pptx",
        "application/pptx",
        MAX_DOCUMENT_BYTES,
    ),
    "application/vnd.ms-excel": (".xlsx", "application/vnd.ms-excel", MAX_DOCUMENT_BYTES),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        ".xlsx",
        "application/vnd.ms-excel",
        MAX_DOCUMENT_BYTES,
    ),
    "image/jpeg": (".jpg", "image/jpeg", MAX_IMAGE_BYTES),
    "image/png": (".png", "image/png", MAX_IMAGE_BYTES),
    "image/tiff": (".tiff", "image/tiff", MAX_IMAGE_BYTES),
    "image/bmp": (".bmp", "image/bmp", MAX_IMAGE_BYTES),
}


TRANSPORT_DOCUMENT_TYPES = {
    "flight_ticket",
    "boarding_pass",
    "train_ticket",
    "bus_ticket",
    "travel_ticket",
}
FLIGHT_NUMBER_RE = re.compile(
    r"\b((?:[A-ZА-Я]{2,3}|[A-ZА-Я]\d|\d[A-ZА-Я]))[\s-]?(\d{2,4})\b",
    re.IGNORECASE,
)
LOCATION_GENERIC_TOKENS = {
    "аэропорт",
    "airport",
    "терминал",
    "terminal",
    "город",
    "city",
}


def _event_datetime(event: dict, field: str) -> datetime | None:
    raw = str(event.get(field) or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def _flight_number(event: dict) -> str:
    haystack = f"{event.get('title') or ''} {event.get('description') or ''}"
    match = FLIGHT_NUMBER_RE.search(haystack.upper())
    if not match:
        return ""
    return f"{match.group(1)}{match.group(2)}".upper().replace("-", "").replace(" ", "")


def _location_tokens(value: object) -> set[str]:
    return {
        token.casefold().replace("ё", "е")
        for token in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", str(value or ""))
        if len(token) > 1 and token.casefold().replace("ё", "е") not in LOCATION_GENERIC_TOKENS
    }


def _locations_similar(left: object, right: object) -> bool:
    a = _location_tokens(left)
    b = _location_tokens(right)
    if not a or not b:
        return False
    return len(a & b) / max(1, min(len(a), len(b))) >= 0.6


def _same_route(left: dict, right: dict) -> bool:
    left_start = left.get("start_location")
    right_start = right.get("start_location")
    left_end = left.get("end_location")
    right_end = right.get("end_location")
    return _locations_similar(left_start, right_start) and _locations_similar(left_end, right_end)


def _same_transport_action(left: dict, right: dict) -> bool:
    left_source = left.get("source") if isinstance(left.get("source"), dict) else {}
    right_source = right.get("source") if isinstance(right.get("source"), dict) else {}
    left_message = str(left_source.get("provider_message_id") or "").strip()
    right_message = str(right_source.get("provider_message_id") or "").strip()
    if not left_message or left_message != right_message:
        return False

    left_event = left.get("attachment_event")
    right_event = right.get("attachment_event")
    if not isinstance(left_event, dict) or not isinstance(right_event, dict):
        return False
    if not left_event.get("movement") or not right_event.get("movement"):
        return False

    left_start = _event_datetime(left_event, "start")
    right_start = _event_datetime(right_event, "start")
    left_end = _event_datetime(left_event, "end")
    right_end = _event_datetime(right_event, "end")
    if None in {left_start, right_start, left_end, right_end}:
        return False

    left_flight = _flight_number(left_event)
    right_flight = _flight_number(right_event)
    if left_flight and right_flight and left_flight != right_flight:
        return False

    same_route = _same_route(left_event, right_event)
    if left_flight and right_flight:
        if not same_route and all(
            (
                left_event.get("start_location"),
                right_event.get("start_location"),
                left_event.get("end_location"),
                right_event.get("end_location"),
            )
        ):
            return False
        return (
            abs((left_start - right_start).total_seconds()) <= 2 * 60 * 60
            and abs((left_end - right_end).total_seconds()) <= 2 * 60 * 60
        )

    return (
        same_route
        and abs((left_start - right_start).total_seconds()) <= 30 * 60
        and abs((left_end - right_end).total_seconds()) <= 45 * 60
    )


def _action_quality(action: dict) -> tuple[float, int, int]:
    event = action.get("attachment_event") if isinstance(action.get("attachment_event"), dict) else {}
    try:
        confidence = float(action.get("confidence") or event.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    completeness = sum(
        bool(event.get(field))
        for field in (
            "start",
            "end",
            "start_location",
            "end_location",
            "start_timezone",
            "end_timezone",
            "description",
        )
    )
    recognized = int(str(action.get("attachment_document_type") or "") in TRANSPORT_DOCUMENT_TYPES)
    return confidence, int(bool(action.get("ready"))), completeness + recognized


def _append_deduped_action(actions: list[dict], action: dict) -> bool:
    for index, current in enumerate(actions):
        if not _same_transport_action(current, action):
            continue
        if _action_quality(action) > _action_quality(current):
            actions[index] = action
        return False
    actions.append(action)
    return True


def _attachment_type(filename: object, mime_type: object = None) -> tuple[str, str, int, str] | None:
    raw_name = Path(str(filename or "")).name[:255]
    suffix = Path(raw_name).suffix.lower()
    config = SUPPORTED_TYPES.get(suffix)
    if config is not None:
        mimetype, limit = config
        return raw_name, mimetype, limit, suffix

    mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    mime_config = SUPPORTED_MIME_TYPES.get(mime)
    if mime_config is None:
        return None
    fallback_suffix, mimetype, limit = mime_config
    display_name = raw_name or f"attachment{fallback_suffix}"
    return display_name, mimetype, limit, fallback_suffix


def _duration_minutes(event: dict) -> int | None:
    try:
        start = datetime.fromisoformat(str(event.get("start") or "").replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(event.get("end") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        return None
    minutes = int((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() // 60)
    return max(5, min(60 * 24 * 7, minutes)) if minutes > 0 else None


def _source(account: dict, message: dict, source_index: int, filename: str, attachment: dict) -> dict:
    return {
        "index": source_index,
        "subject": str(message.get("subject") or "Без темы")[:300],
        "from": str(message.get("from") or "")[:300],
        "account": account.get("display_name") or account.get("email") or account.get("provider"),
        "attachment": filename,
        "provider_message_id": str(message.get("provider_message_id") or "")[:500],
        "attachment_id": str(attachment.get("attachment_id") or attachment.get("part_index") or "")[:500],
    }


def _fetch_bytes(user_id: int, account: dict, message: dict, attachment: dict, *, limit: int) -> bytes:
    provider = str(account.get("provider") or "").strip().lower()
    if provider == "gmail":
        full = get_email_account(user_id, int(account["account_id"]), with_credentials=True)
        credentials = (full or {}).get("credentials") or {}
        token = credentials.get("token")
        message_id = str(message.get("provider_message_id") or "").strip()
        if not isinstance(token, dict) or not message_id:
            raise ValueError("Не удалось получить Gmail-вложение")
        return fetch_gmail_attachment_bytes(token, message_id, attachment, max_bytes=limit)
    if provider in {"yandex", "mailru"}:
        raw = message.get("_raw_message")
        if not isinstance(raw, (bytes, bytearray)):
            raise ValueError("Содержимое IMAP-письма недоступно")
        return extract_imap_attachment_bytes(bytes(raw), attachment, max_bytes=limit)
    raise ValueError("Неподдерживаемый почтовый провайдер")


def _action_from_event(
    event: dict,
    *,
    account: dict,
    message: dict,
    attachment: dict,
    source_index: int,
    filename: str,
    document_type: str,
    document_summary: str,
    document_warnings: list[str],
) -> dict:
    warnings = [
        str(item).strip()[:300]
        for item in [*document_warnings, *(event.get("warnings") or [])]
        if str(item).strip()
    ][:10]
    route = str(event.get("location") or "").strip()
    reason = f"Найдено во вложении «{filename}»"
    if document_summary:
        reason += f": {document_summary}"
    if route:
        reason += f" · {route}"
    return {
        "action_type": "calendar_event",
        "title": str(event.get("title") or "Событие из вложения")[:300],
        "due_at": event.get("start"),
        "duration_minutes": _duration_minutes(event),
        "confidence": float(event.get("confidence") or 0),
        "reason": reason[:700],
        "source": _source(account, message, source_index, filename, attachment),
        "attachment_document_type": document_type,
        "attachment_event": event,
        "ready": bool(event.get("ready")),
        "warnings": warnings,
    }


def analyze_email_attachments(
    user_id: int,
    messages: list[tuple[dict, dict]],
    *,
    user_timezone: str,
) -> dict:
    """Analyze a bounded number of supported attachments from recent messages."""
    actions: list[dict] = []
    warnings: list[str] = []
    analyzed = 0
    detected = 0
    supported_found = 0
    deduplicated = 0

    for source_index, (account, message) in enumerate(messages, 1):
        for attachment in message.get("attachments") or []:
            if not isinstance(attachment, dict):
                continue
            detected += 1
            typed = _attachment_type(attachment.get("filename"), attachment.get("mime_type"))
            if typed is None:
                continue
            supported_found += 1
            filename, mimetype, limit, provider_suffix = typed
            try:
                declared_size = int(attachment.get("size") or 0)
            except (TypeError, ValueError):
                declared_size = 0
            if declared_size > limit:
                warnings.append(f"«{filename}» пропущен: файл больше допустимого размера.")
                continue
            if analyzed >= MAX_ATTACHMENTS_ANALYZED:
                continue
            analyzed += 1
            try:
                content = _fetch_bytes(user_id, account, message, attachment, limit=limit)
                result = analyze_file_bytes(
                    content,
                    filename=f"document{provider_suffix}",
                    mimetype=mimetype,
                    user_timezone=user_timezone,
                )
            except Exception as exc:
                logger.warning(
                    "Email attachment analysis failed user=%s account=%s file=%s mime=%s (%s)",
                    user_id,
                    account.get("account_id"),
                    filename,
                    attachment.get("mime_type"),
                    type(exc).__name__,
                )
                warnings.append(f"Не удалось надёжно разобрать вложение «{filename}».")
                continue

            document_type = str(result.get("document_type") or "other").strip().lower()[:60]
            document_summary = " ".join(str(result.get("summary") or "").split()).strip()[:300]
            document_warnings = [str(item)[:300] for item in (result.get("warnings") or [])]
            for event in result.get("events") or []:
                action = _action_from_event(
                    event,
                    account=account,
                    message=message,
                    attachment=attachment,
                    source_index=source_index,
                    filename=filename,
                    document_type=document_type,
                    document_summary=document_summary,
                    document_warnings=document_warnings,
                )
                if not _append_deduped_action(actions, action):
                    deduplicated += 1
                if len(actions) >= MAX_ATTACHMENT_ACTIONS:
                    break
            if len(actions) >= MAX_ATTACHMENT_ACTIONS:
                break
        if len(actions) >= MAX_ATTACHMENT_ACTIONS:
            break

    if detected and not supported_found:
        warnings.append("Вложения в письмах найдены, но их формат пока не поддерживается для ИИ-разбора.")
    if supported_found > analyzed:
        warnings.append(
            f"Поддерживаемых вложений найдено {supported_found}; за один раз анализируются максимум {MAX_ATTACHMENTS_ANALYZED}."
        )
    return {
        "actions": actions,
        "warnings": warnings[:10],
        "analyzed": analyzed,
        "detected": detected,
        "supported_found": supported_found,
        "deduplicated": deduplicated,
    }
