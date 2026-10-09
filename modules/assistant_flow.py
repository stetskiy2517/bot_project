"""Shared deterministic-first recovery for unhandled planner actions.

This layer may use AI to interpret an action, but every side effect still goes
through the existing deterministic task/calendar/reminder/note handlers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from core.user_activity_store import set_request_diagnostic
from modules.ai_assistant import interpret_unhandled_action
from modules.ai_task_planner import handle_unhandled_task_plan
from modules.router import route_text

RouteFunc = Callable[..., Awaitable[bool]]


async def recover_unhandled_action(
    update,
    context,
    text: str,
    *,
    user_id: int,
    history: list[dict] | None = None,
    route_func: RouteFunc | None = None,
) -> bool:
    """Recover a planner action without letting the model execute it directly."""
    route = route_func or route_text

    handled = await handle_unhandled_task_plan(
        update,
        context,
        text,
        history=history,
    )
    if handled:
        set_request_diagnostic(ai_fallback="task_plan")
        return True

    rewritten = await asyncio.to_thread(
        interpret_unhandled_action,
        text,
        user_id=user_id,
    )
    if not rewritten or rewritten.casefold() == str(text or "").casefold():
        return False

    handled = await route(update, context, text=rewritten)
    if handled:
        set_request_diagnostic(ai_fallback="action_rewrite")
        return True
    return False
