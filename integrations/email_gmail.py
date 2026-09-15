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


def _service(token: dict):
    credentials = Credentials.from_authorized_user_info(token, scopes=[GMAIL_SCOPE])
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _decode_body_data(value: str | None, charset: str = "utf-8") -> str:
    if not value:
        return ""
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeError):
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
            "from": headers.get("from", ""),
            "subject": headers.get("subject") or "Без темы",
            "date": _normalized_date(headers.get("date", "")),
            "preview": body[:MAX_PREVIEW],
            "body": body,
        })
    return results
