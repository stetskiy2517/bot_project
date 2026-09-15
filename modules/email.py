from __future__ import annotations

import logging
import re

from core.email_store import get_email_account, list_email_accounts
from integrations.email_gmail import list_messages as list_gmail_messages
from integrations.email_imap import list_messages as list_imap_messages

logger = logging.getLogger(__name__)
EMAIL_INTENT_RE = re.compile(r"\b(?:почт\w*|письм\w*|email|e-mail|входящ\w*)\b", re.IGNORECASE)
UNREAD_RE = re.compile(r"\b(?:непрочитан\w*|нов\w+\s+письм\w*)\b", re.IGNORECASE)
SEARCH_WITH_PREPOSITION_RE = re.compile(
    r"(?:найди|поищи|покажи|есть ли)\s+(?:мне\s+)?(?:письм\w*\s+)?(?:от|про|по теме)\s+(.+)$",
    re.IGNORECASE,
)
SEARCH_DIRECT_RE = re.compile(
    r"(?:найди|поищи)\s+(?:мне\s+)?письм\w*\s+(.+)$",
    re.IGNORECASE,
)


def detect_email_intent(text: str) -> bool:
    return bool(EMAIL_INTENT_RE.search(str(text or "")))


def _query_from_text(text: str) -> str | None:
    clean = str(text or "").strip()
    match = SEARCH_WITH_PREPOSITION_RE.search(clean) or SEARCH_DIRECT_RE.search(clean)
    if not match:
        return None
    value = " ".join(match.group(1).split()).strip(" .,!?:;\"'«»")
    return value or None


def _read_account(user_id: int, account: dict, *, query: str | None, unread_only: bool, limit: int) -> list[dict]:
    full = get_email_account(user_id, int(account["account_id"]), with_credentials=True)
    if not full:
        return []
    credentials = full["credentials"]
    if full["provider"] == "gmail":
        return list_gmail_messages(credentials["token"], limit=limit, query=query, unread_only=unread_only)
    return list_imap_messages(
        full["provider"],
        full["email"],
        credentials["app_password"],
        limit=limit,
        query=query,
        unread_only=unread_only,
    )


def _format(messages: list[tuple[dict, dict]]) -> str:
    if not messages:
        return "Подходящих писем не нашёл."
    lines = []
    for index, (account, message) in enumerate(messages[:10], 1):
        provider = account.get("display_name") or account.get("email") or account.get("provider")
        subject = str(message.get("subject") or "Без темы").strip()
        sender = str(message.get("from") or "Неизвестный отправитель").strip()
        preview = str(message.get("preview") or "").strip()
        line = f"{index}. {subject}\nОт: {sender}\nЯщик: {provider}"
        if preview:
            line += f"\n{preview[:240]}"
        lines.append(line)
    return "\n\n".join(lines)


def answer_email_query(user_id: int, text: str) -> str:
    accounts = [account for account in list_email_accounts(user_id) if account.get("enabled")]
    if not accounts:
        return "Почта ещё не подключена. Открой настройки и добавь Gmail, Яндекс или Mail.ru."
    unread_only = bool(UNREAD_RE.search(text))
    query = _query_from_text(text)
    collected: list[tuple[dict, dict]] = []
    errors = []
    for account in accounts:
        try:
            for message in _read_account(user_id, account, query=query, unread_only=unread_only, limit=10):
                collected.append((account, message))
        except Exception:
            logger.exception("Failed to read email account %s for user %s", account.get("account_id"), user_id)
            errors.append(account.get("email") or account.get("provider"))
    if not collected and errors:
        return "Не удалось прочитать почту. Проверь подключение ящика в настройках."
    return _format(collected)


async def handle_email_text(update, context, text: str) -> bool:
    user_id = getattr(update.effective_user, "id", None)
    if user_id is None:
        return False
    await update.message.reply_text(answer_email_query(user_id, text))
    return True
