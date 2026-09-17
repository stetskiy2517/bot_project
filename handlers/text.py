"""Telegram text transport using the same deterministic-first assistant flow as web."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.chat_context import append_chat_exchange, recent_chat_messages
from modules.ai_assistant import answer_unhandled
from modules.router import route_text

logger = logging.getLogger(__name__)


class _RecordingMessage:
    """Forward Telegram replies while keeping a bounded copy for dialogue context."""

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


class _RecordingUpdate:
    """Delegate the original Telegram update but expose the recording message wrapper."""

    def __init__(self, update: Any, text: str):
        self._update = update
        self.message = _RecordingMessage(update.message, text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._update, name)


def _context_reply(replies: list[str]) -> str:
    """Keep one compact deterministic answer in short-term dialogue context."""
    clean = [" ".join(str(item).split()).strip() for item in replies if str(item).strip()]
    return "\n".join(clean[:2])[:4000]


async def handle_message_text(update: Any, context: Any, text: str) -> bool:
    """Run deterministic modules first, then the shared AI fallback when available."""
    if not getattr(update, "message", None) or not getattr(update, "effective_user", None):
        return False

    candidate = " ".join(str(text or "").split()).strip()
    if not candidate:
        return False

    user_id = int(update.effective_user.id)
    proxy = _RecordingUpdate(update, candidate)
    handled = await route_text(proxy, context, text=candidate)
    if handled:
        recorded = _context_reply(proxy.message.replies)
        if recorded:
            append_chat_exchange(user_id, candidate, recorded)
        return True

    history = recent_chat_messages(user_id)
    try:
        answer = await asyncio.to_thread(
            answer_unhandled,
            candidate,
            user_id=user_id,
            history=history,
        )
    except Exception:
        logger.exception("Telegram AI fallback failed for user %s", user_id)
        answer = None

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
