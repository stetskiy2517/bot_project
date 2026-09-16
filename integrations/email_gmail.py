from __future__ import annotations

import base64
from email.utils import parsedate_to_datetime
from html import unescape
import re

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MAX_PREVIEW = 700
MAX_BODY = 6000
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

DOCUMENT_MIME_SUFFIXES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/epub": ".epub",
    "application/epub+zip": ".epub",
    "application/ppt": ".ppt",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/pptx": ".pptx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-excel": ".xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}


def _service(token: dict):
    credentials = Credentials.from_authorized_user_info(token, scopes=[GMAIL_SCOPE])
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _decode_urlsafe_bytes(value: str | None) -> bytes:
    if not value:
        return b""
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeError):
        return b""


def _decode_body_data(value: str | None, charset: str = "utf-8") -> str:
    raw = _decode_urlsafe_bytes(value)
    if not raw:
        return ""
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _headers(payload: dict) -> dict[str, str]:
    return {
        str(entry.get("name") or "").lower(): str(entry.get("value") or "")
        for entry in payload.get("headers", [])
        if isinstance(entry, dict)
    }


def _charset(payload: dict) -> str:
    content_type = _headers(payload).get("content-type", "")
    match = re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type, flags=re.IGNORECASE)
    return match.group(1).strip() if match else "utf-8"


def _html_text(value: str) -> str:
    clean = re.sub(r"(?is)<(?:script|style).*?>.*?</(?:script|style)>", " ", value)
    clean = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>|</li\s*>", "\n", clean)
    clean = re.sub(r"(?s)<[^>]+>", " ", clean)
    return unescape(clean)


def _payload_text(payload: dict) -> str:
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict) -> None:
        if not isinstance(part, dict):
            return
        filename = str(part.get("filename") or "").strip()
        mime = str(part.get("mimeType") or "").lower()
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        if not filename and mime in {"text/plain", "text/html"}:
            text = _decode_body_data(body.get("data"), _charset(part))
            if text:
                if mime == "text/plain":
                    plain.append(text)
                else:
                    html.append(_html_text(text))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    source = "\n".join(plain) if plain else "\n".join(html)
    clean = re.sub(r"[ \t]+", " ", unescape(source))
    clean = re.sub(r"\n\s*\n+", "\n", clean)
    clean = re.sub(r"\s*\n\s*", "\n", clean).strip()
    return clean[:MAX_BODY]


def _attachment_metadata(payload: dict) -> list[dict]:
    attachments: list[dict] = []

    def walk(part: dict) -> None:
        if not isinstance(part, dict):
            return
        filename = str(part.get("filename") or "").strip()
        mime = str(part.get("mimeType") or "application/octet-stream").split(";", 1)[0].strip().lower()
        headers = _headers(part)
        disposition = headers.get("content-disposition", "").lower()
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        attachment_id = str(body.get("attachmentId") or "").strip()
        document_without_name = bool(attachment_id and mime in DOCUMENT_MIME_SUFFIXES)
        is_attachment = bool(filename or "attachment" in disposition or document_without_name)
        if is_attachment:
            try:
                size = max(0, int(body.get("size") or 0))
            except (TypeError, ValueError):
                size = 0
            if not filename:
                filename = f"attachment{DOCUMENT_MIME_SUFFIXES.get(mime, '')}" or "attachment"
            attachments.append(
                {
                    "filename": filename[:255],
                    "mime_type": mime[:200],
                    "size": size,
                    "attachment_id": attachment_id[:500] or None,
                    "part_id": str(part.get("partId") or "")[:100] or None,
                }
            )
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return attachments


def _find_part(payload: dict, part_id: str) -> dict | None:
    if not isinstance(payload, dict):
        return None
    if str(payload.get("partId") or "") == part_id:
        return payload
    for child in payload.get("parts", []) or []:
        found = _find_part(child, part_id)
        if found is not None:
            return found
    return None


def fetch_attachment_bytes(
    token: dict,
    message_id: str,
    attachment: dict,
    *,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> bytes:
    """Fetch one Gmail attachment without persisting it locally."""
    limit = max(1, int(max_bytes))
    try:
        declared_size = int(attachment.get("size") or 0)
    except (TypeError, ValueError):
        declared_size = 0
    if declared_size > limit:
        raise ValueError("Вложение превышает допустимый размер")

    service = _service(token)
    attachment_id = str(attachment.get("attachment_id") or "").strip()
    if attachment_id:
        result = service.users().messages().attachments().get(
            userId="me",
            messageId=str(message_id),
            id=attachment_id,
        ).execute()
        data = _decode_urlsafe_bytes(result.get("data"))
    else:
        part_id = str(attachment.get("part_id") or "").strip()
        if not part_id:
            raise ValueError("Вложение не содержит идентификатора части письма")
        message = service.users().messages().get(
            userId="me",
            id=str(message_id),
            format="full",
        ).execute()
        payload = message.get("payload", {}) if isinstance(message.get("payload"), dict) else {}
        part = _find_part(payload, part_id)
        body = part.get("body") if isinstance(part, dict) and isinstance(part.get("body"), dict) else {}
        data = _decode_urlsafe_bytes(body.get("data"))

    if not data:
        raise ValueError("Не удалось получить содержимое вложения")
    if len(data) > limit:
        raise ValueError("Вложение превышает допустимый размер")
    return data


def _normalized_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return value
    return parsed.isoformat() if parsed else value


def list_messages(token: dict, *, limit: int = 10, query: str | None = None, unread_only: bool = False) -> list[dict]:
    service = _service(token)
    parts = []
    if unread_only:
        parts.append("is:unread")
    if query:
        parts.append(str(query).strip())
    response = service.users().messages().list(
        userId="me",
        maxResults=max(1, min(int(limit), 30)),
        q=" ".join(parts),
    ).execute()
    results = []
    for item in response.get("messages", []):
        message = service.users().messages().get(
            userId="me",
            id=item["id"],
            format="full",
        ).execute()
        payload = message.get("payload", {}) if isinstance(message.get("payload"), dict) else {}
        headers = _headers(payload)
        body = _payload_text(payload)
        if not body:
            body = str(message.get("snippet") or "").strip()[:MAX_BODY]
        results.append({
            "provider_message_id": str(message.get("id") or item.get("id") or ""),
            "from": headers.get("from", ""),
            "subject": headers.get("subject") or "Без темы",
            "date": _normalized_date(headers.get("date", "")),
            "preview": body[:MAX_PREVIEW],
            "body": body,
            "attachments": _attachment_metadata(payload),
        })
    return results
