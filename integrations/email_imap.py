from __future__ import annotations

import email
import imaplib
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from html import unescape
import re

from core.email_store import PROVIDERS

MAX_FETCH = 50
MAX_PREVIEW = 500


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()


def _plain_text(message: Message) -> str:
    chunks: list[str] = []
    if message.is_multipart():
        parts = message.walk()
    else:
        parts = [message]
    for part in parts:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        if part.get_content_type() != "text/plain":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        chunks.append(text)
    clean = re.sub(r"\s+", " ", unescape(" ".join(chunks))).strip()
    return clean[:MAX_PREVIEW]


def _message_payload(raw: bytes) -> dict:
    message = email.message_from_bytes(raw)
    date_value = _decode(message.get("Date"))
    try:
        parsed = parsedate_to_datetime(date_value).isoformat() if date_value else None
    except (TypeError, ValueError, OverflowError):
        parsed = None
    return {
        "from": _decode(message.get("From")),
        "subject": _decode(message.get("Subject")) or "Без темы",
        "date": parsed or date_value,
        "preview": _plain_text(message),
    }


def _connect(provider: str, address: str, password: str):
    config = PROVIDERS.get(provider)
    if not config or not config.get("imap_host"):
        raise ValueError("Для этого провайдера IMAP не поддерживается")
    client = imaplib.IMAP4_SSL(config["imap_host"], int(config["imap_port"]), timeout=15)
    client.login(address, password)
    return client


def test_connection(provider: str, address: str, password: str) -> None:
    client = _connect(provider, address, password)
    try:
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Не удалось открыть входящие")
    finally:
        try:
            client.logout()
        except Exception:
            pass


def list_messages(provider: str, address: str, password: str, *, limit: int = 10, query: str | None = None, unread_only: bool = False) -> list[dict]:
    limit = max(1, min(int(limit), 30))
    query_normalized = " ".join(str(query or "").casefold().split())
    client = _connect(provider, address, password)
    try:
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Не удалось открыть входящие")
        criteria = "UNSEEN" if unread_only else "ALL"
        status, data = client.search(None, criteria)
        if status != "OK" or not data:
            return []
        ids = data[0].split()[-MAX_FETCH:]
        results: list[dict] = []
        for message_id in reversed(ids):
            status, payload = client.fetch(message_id, "(RFC822)")
            if status != "OK" or not payload:
                continue
            raw = next((item[1] for item in payload if isinstance(item, tuple) and len(item) > 1), None)
            if not isinstance(raw, (bytes, bytearray)):
                continue
            item = _message_payload(bytes(raw))
            if query_normalized:
                haystack = " ".join((item["from"], item["subject"], item["preview"])).casefold()
                if query_normalized not in haystack:
                    continue
            results.append(item)
            if len(results) >= limit:
                break
        return results
    finally:
        try:
            client.logout()
        except Exception:
            pass
