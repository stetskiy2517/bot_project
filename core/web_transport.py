"""Web transport adapter for the existing Smart Planner router.

The calendar/router layer still consumes a small Telegram-like interface.
This adapter lets web/PWA requests use the same tested core without calling
Telegram API. Deeper transport decoupling can happen incrementally later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


@dataclass
class WebMessage:
    text: str
    replies: list[str] = field(default_factory=list)

    async def reply_text(self, text: str, **_: Any) -> None:
        self.replies.append(str(text))


class WebUpdate:
    def __init__(self, user_id: int, user_name: str, text: str):
        self.message = WebMessage(text=text)
        self.effective_user = SimpleNamespace(id=user_id, full_name=user_name)
        self.callback_query = None


class WebContext:
    def __init__(self, user_data: dict[str, Any]):
        self.user_data = user_data
        self.args: list[str] = []


@dataclass
class WebPlannerResult:
    handled: bool
    replies: list[str]
