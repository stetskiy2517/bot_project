"""Ephemeral AI analysis of supported email attachments.

Attachments are fetched into memory, passed through the same file-ingest pipeline as
manual uploads, and never persisted by this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path

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
                actions.append(
                    _action_from_event(
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
                )
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
    }
