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
HEADER_SCAN_BATCH = 50
MAX_PREVIEW = 700
MAX_BODY = 6000
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

MIME_SUFFIXES = {
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
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
}


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


def _decoded_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _html_text(value: str) -> str:
    clean = re.sub(r"(?is)<(?:script|style).*?>.*?</(?:script|style)>", " ", value)
    clean = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>|</li\s*>", "\n", clean)
    clean = re.sub(r"(?s)<[^>]+>", " ", clean)
    return unescape(clean)


def _message_text(message: Message) -> str:
    plain: list[str] = []
    html: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        text = _decoded_part(part)
        if not text:
            continue
        if content_type == "text/plain":
            plain.append(text)
        else:
            html.append(_html_text(text))
    source = "\n".join(plain) if plain else "\n".join(html)
    clean = re.sub(r"[ \t]+", " ", unescape(source))
    clean = re.sub(r"\n\s*\n+", "\n", clean)
    clean = re.sub(r"\s*\n\s*", "\n", clean).strip()
    return clean[:MAX_BODY]


def _attachment_metadata(message: Message) -> list[dict]:
    attachments: list[dict] = []
    parts = list(message.walk()) if message.is_multipart() else [message]
    for index, part in enumerate(parts):
        if part.get_content_maintype() == "multipart":
            continue
        content_type = str(part.get_content_type() or "application/octet-stream").lower()
        filename = _decode(part.get_filename())
        disposition = str(part.get_content_disposition() or "").lower()
        if not filename and disposition != "attachment":
            continue
        if not filename:
            filename = f"attachment{MIME_SUFFIXES.get(content_type, '')}"
        payload = part.get_payload(decode=True)
        size = len(payload) if isinstance(payload, (bytes, bytearray)) else 0
        attachments.append(
            {
                "filename": filename[:255],
                "mime_type": content_type[:200],
                "size": size,
                "part_index": index,
            }
        )
    return attachments


def extract_attachment_bytes(
    raw: bytes,
    attachment: dict,
    *,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> bytes:
    """Decode one attachment from an already fetched RFC822 message."""
    limit = max(1, int(max_bytes))
    try:
        declared_size = int(attachment.get("size") or 0)
        part_index = int(attachment.get("part_index"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректные данные вложения") from exc
    if declared_size > limit:
        raise ValueError("Вложение превышает допустимый размер")

    message = email.message_from_bytes(raw)
    parts = list(message.walk()) if message.is_multipart() else [message]
    if part_index < 0 or part_index >= len(parts):
        raise ValueError("Вложение больше не найдено в письме")
    part = parts[part_index]
    payload = part.get_payload(decode=True)
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        raise ValueError("Не удалось получить содержимое вложения")
    data = bytes(payload)
    if len(data) > limit:
        raise ValueError("Вложение превышает допустимый размер")
    return data


def _message_payload(raw: bytes) -> dict:
    message = email.message_from_bytes(raw)
    date_value = _decode(message.get("Date"))
    try:
        parsed = parsedate_to_datetime(date_value).isoformat() if date_value else None
    except (TypeError, ValueError, OverflowError):
        parsed = None
    body = _message_text(message)
    attachments = _attachment_metadata(message)
    return {
        "from": _decode(message.get("From")),
        "subject": _decode(message.get("Subject")) or "Без темы",
        "date": parsed or date_value,
        "preview": body[:MAX_PREVIEW],
        "body": body,
        "attachments": attachments,
        "_raw_message": raw if attachments else None,
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


def _quoted_search_value(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _server_search(client, query: str, *, unread_only: bool) -> list[bytes] | None:
    try:
        query.encode("ascii")
    except UnicodeEncodeError:
        return None

    value = _quoted_search_value(query)
    criteria: list[str] = []
    if unread_only:
        criteria.append("UNSEEN")
    criteria.extend(("OR", "OR", "FROM", value, "SUBJECT", value, "BODY", value))
    try:
        status, data = client.search(None, *criteria)
    except (imaplib.IMAP4.error, UnicodeError, ValueError, OSError):
        return None
    if status != "OK":
        return None
    if not data or not data[0]:
        return []
    return data[0].split()


def _base_ids(client, *, unread_only: bool) -> list[bytes]:
    criteria = "UNSEEN" if unread_only else "ALL"
    try:
        status, data = client.search(None, criteria)
    except (imaplib.IMAP4.error, OSError) as exc:
        raise EmailTransportError("Почтовый сервер не смог выполнить поиск во входящих.") from exc
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _header_raw_from_payload(payload) -> bytes | None:
    if not payload:
        return None
    for entry in payload:
        if not isinstance(entry, tuple) or len(entry) < 2:
            continue
        raw = entry[1]
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
    return None


def _scan_header_one(client, message_id: bytes, query: str) -> bool:
    try:
        status, payload = client.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
    except (imaplib.IMAP4.error, OSError, ValueError):
        return False
    if status != "OK":
        return False
    raw = _header_raw_from_payload(payload)
    return bool(raw and _header_matches(raw, query))


def _fallback_header_search(client, ids: list[bytes], query: str, *, limit: int) -> list[bytes]:
    candidates = ids[-MAX_HEADER_SCAN:]
    matches: list[bytes] = []
    end = len(candidates)
    while end > 0 and len(matches) < limit:
        start = max(0, end - HEADER_SCAN_BATCH)
        batch = candidates[start:end]
        message_set = ",".join(item.decode("ascii") for item in batch)
        batch_matches: list[bytes] = []
        parsed_any = False
        batch_ok = False
        try:
            status, payload = client.fetch(message_set, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            batch_ok = status == "OK"
        except (imaplib.IMAP4.error, OSError, ValueError):
            payload = None

        if batch_ok and payload:
            for entry in payload:
                if not isinstance(entry, tuple) or len(entry) < 2:
                    continue
                meta, raw = entry[0], entry[1]
                if not isinstance(meta, (bytes, bytearray)) or not isinstance(raw, (bytes, bytearray)):
                    continue
                match = re.match(rb"^(\d+)", bytes(meta))
                if not match:
                    continue
                parsed_any = True
                if _header_matches(bytes(raw), query):
                    batch_matches.append(match.group(1))

        if batch_ok and parsed_any:
            matches.extend(sorted(batch_matches, key=int, reverse=True))
        else:
            for message_id in reversed(batch):
                if _scan_header_one(client, message_id, query):
                    matches.append(message_id)
                    if len(matches) >= limit:
                        break
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
    return [], True


def list_messages(provider: str, address: str, password: str, *, limit: int = 10, query: str | None = None, unread_only: bool = False) -> list[dict]:
    limit = max(1, min(int(limit), 30))
    query_normalized = " ".join(str(query or "").casefold().split())
    client = _connect(provider, address, password)
    try:
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise EmailTransportError("Не удалось открыть входящие")
        ids, already_filtered = _search_ids(
            client,
            query_normalized,
            unread_only=unread_only,
            limit=limit,
        )
        results: list[dict] = []
        for message_id in reversed(ids):
            try:
                status, payload = client.fetch(message_id, "(RFC822)")
            except (imaplib.IMAP4.error, OSError, ValueError):
                continue
            if status != "OK" or not payload:
                continue
            raw = next((item[1] for item in payload if isinstance(item, tuple) and len(item) > 1), None)
            if not isinstance(raw, (bytes, bytearray)):
                continue
            item = _message_payload(bytes(raw))
            item["provider_message_id"] = message_id.decode("ascii", errors="ignore")
            if query_normalized and not already_filtered:
                haystack = " ".join((item["from"], item["subject"], item.get("body") or item["preview"])).casefold()
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
