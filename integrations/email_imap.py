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
MAX_HEADER_SCAN = 5000
HEADER_SCAN_BATCH = 100
MAX_PREVIEW = 500


class EmailAuthenticationError(RuntimeError):
    pass


class EmailTransportError(RuntimeError):
    pass


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


def _header_matches(raw: bytes, query: str) -> bool:
    message = email.message_from_bytes(raw)
    haystack = " ".join((_decode(message.get("From")), _decode(message.get("Subject")))).casefold()
    return query in haystack


def _auth_error_message(provider: str) -> str:
    if provider == "yandex":
        return (
            "Яндекс отклонил вход. Если пароль приложения создан только что, подожди 2–3 часа. "
            "Проверь в Яндекс Почте: Настройки → Почтовые программы → включены IMAP и "
            "«Пароли приложений и OAuth-токены»."
        )
    if provider == "mailru":
        return "Mail.ru отклонил вход. Проверь адрес ящика и пароль приложения для почты."
    return "Почтовый сервер отклонил вход. Проверь адрес ящика и пароль приложения."


def _connect(provider: str, address: str, password: str):
    config = PROVIDERS.get(provider)
    if not config or not config.get("imap_host"):
        raise ValueError("Для этого провайдера IMAP не поддерживается")
    address = str(address or "").strip()
    password = str(password or "").strip()
    if not address or not password:
        raise ValueError("Не указан email или пароль приложения")
    try:
        client = imaplib.IMAP4_SSL(config["imap_host"], int(config["imap_port"]), timeout=15)
    except (OSError, TimeoutError) as exc:
        raise EmailTransportError("Не удалось связаться с почтовым сервером. Попробуй ещё раз позже.") from exc
    try:
        client.login(address, password)
    except imaplib.IMAP4.error as exc:
        try:
            client.logout()
        except Exception:
            pass
        raise EmailAuthenticationError(_auth_error_message(provider)) from exc
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


def _quoted_search_value(query: str) -> bytes:
    escaped = query.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'.encode("utf-8")


def _server_search(client, query: str, *, unread_only: bool) -> list[bytes] | None:
    value = _quoted_search_value(query)
    criteria: list[str | bytes] = []
    if unread_only:
        criteria.append("UNSEEN")
    criteria.extend(("OR", "OR", "FROM", value, "SUBJECT", value, "BODY", value))
    try:
        status, data = client.search("UTF-8", *criteria)
    except (imaplib.IMAP4.error, UnicodeError, ValueError):
        return None
    if status != "OK":
        return None
    if not data or not data[0]:
        return []
    return data[0].split()


def _base_ids(client, *, unread_only: bool) -> list[bytes]:
    criteria = "UNSEEN" if unread_only else "ALL"
    status, data = client.search(None, criteria)
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _fallback_header_search(client, ids: list[bytes], query: str, *, limit: int) -> list[bytes]:
    candidates = ids[-MAX_HEADER_SCAN:]
    matches: list[bytes] = []
    end = len(candidates)
    while end > 0 and len(matches) < limit:
        start = max(0, end - HEADER_SCAN_BATCH)
        batch = candidates[start:end]
        message_set = ",".join(item.decode("ascii") for item in batch)
        status, payload = client.fetch(message_set, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        if status == "OK" and payload:
            batch_matches: list[bytes] = []
            for entry in payload:
                if not isinstance(entry, tuple) or len(entry) < 2:
                    continue
                meta, raw = entry[0], entry[1]
                if not isinstance(meta, (bytes, bytearray)) or not isinstance(raw, (bytes, bytearray)):
                    continue
                match = re.match(rb"^(\d+)", bytes(meta))
                if match and _header_matches(bytes(raw), query):
                    batch_matches.append(match.group(1))
            matches.extend(sorted(batch_matches, key=int, reverse=True))
        end = start
    return sorted(matches[:limit], key=int)


def _search_ids(client, query: str, *, unread_only: bool, limit: int) -> tuple[list[bytes], bool]:
    if query:
        server_ids = _server_search(client, query, unread_only=unread_only)
        if server_ids is not None:
            return server_ids[-limit:], True

    ids = _base_ids(client, unread_only=unread_only)
    if not ids:
        return [], bool(query)
    if not query:
        return ids[-MAX_FETCH:], False

    header_ids = _fallback_header_search(client, ids, query, limit=limit)
    if header_ids:
        return header_ids, True
    return ids[-MAX_FETCH:], False


def list_messages(provider: str, address: str, password: str, *, limit: int = 10, query: str | None = None, unread_only: bool = False) -> list[dict]:
    limit = max(1, min(int(limit), 30))
    query_normalized = " ".join(str(query or "").casefold().split())
    client = _connect(provider, address, password)
    try:
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Не удалось открыть входящие")
        ids, already_filtered = _search_ids(
            client,
            query_normalized,
            unread_only=unread_only,
            limit=limit,
        )
        results: list[dict] = []
        for message_id in reversed(ids):
            status, payload = client.fetch(message_id, "(RFC822)")
            if status != "OK" or not payload:
                continue
            raw = next((item[1] for item in payload if isinstance(item, tuple) and len(item) > 1), None)
            if not isinstance(raw, (bytes, bytearray)):
                continue
            item = _message_payload(bytes(raw))
            if query_normalized and not already_filtered:
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
