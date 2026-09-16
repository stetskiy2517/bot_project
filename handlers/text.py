"""Telegram text transport with the same deterministic-first assistant flow as web."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from core.chat_context import append_chat_exchange, recent_chat_messages
from modules.ai_assistant import answer_unhandled
from modules.router import route_text


class _RecordingMessage:
    """Forward Telegram replies while retaining a bounded representation for context."""

    def __init__(self, message: Any, text: str):
        self._message = message
        self.text = text
        self.replies: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._message, name)

    async def reply_text(self, text: str, **kwargs: Any):
        reply = str(text)
        self.replies.append(reply)
        return await self._message.reply_text(reply, **kwargs)


def _proxy_update(update: Any, text: str):
    return SimpleNamespace(
        message=_RecordingMessage(update.message, text),
        effective_user=update.effective_user,
        callback_query=getattr(update, "callback_query", None),
    )


def _context_reply(replies: list[str]) -> str:
    """Keep one compact deterministic answer in short-term dialogue context."""
    clean = [" ".join(str(item).split()).strip() for item in replies if str(item).strip()]
    return "\n".join(clean[:2])[:4000]


async def handle_message_text(update: Any, context: Any, text: str) -> bool:
    """Run deterministic modules first, then AI fallback when access is enabled."""
    if not getattr(update, "message", None) or not getattr(update, "effective_user", None):
        return False
    candidate = " ".join(str(text or "").split()).strip()
    if not candidate:
        return False

    user_id = int(update.effective_user.id)
    proxy = _proxy_update(update, candidate)
    handled = await route_text(proxy, context, text=candidate)
    if handled:
        recorded = _context_reply(proxy.message.replies)
        if recorded:
            append_chat_exchange(user_id, candidate, recorded)
        return True

    answer = answer_unhandled(
        candidate,
        user_id=user_id,
        history=recent_chat_messages(user_id),
    )
    if answer:
        await update.message.reply_text(answer)
        append_chat_exchange(user_id, candidate, answer)
        return True

    await update.message.reply_text(
        "Не понял команду. Скажи иначе или уточни, что нужно сделать."
    )
    return True


async def handle_text(update: Any, context: Any) -> None:
    text = getattr(getattr(update, "message", None), "text", "")
    await handle_message_text(update, context, str(text or ""))
