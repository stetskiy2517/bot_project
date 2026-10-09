from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from modules.assistant_flow import recover_unhandled_action


class _Message:
    def __init__(self, text: str):
        self.text = text
        self.replies = []

    async def reply_text(self, text: str, **_kwargs):
        self.replies.append(text)


class _Context:
    def __init__(self):
        self.user_data = {}


def _update(text: str):
    return SimpleNamespace(
        message=_Message(text),
        effective_user=SimpleNamespace(id=42),
    )


class AssistantFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_plan_has_priority_over_single_action_rewrite(self):
        update = _update("купить продукты, позвонить маме, сделать домашку")
        context = _Context()
        with (
            patch(
                "modules.assistant_flow.handle_unhandled_task_plan",
                new=AsyncMock(return_value=True),
            ) as planner,
            patch("modules.assistant_flow.interpret_unhandled_action") as rewrite,
        ):
            handled = await recover_unhandled_action(
                update,
                context,
                update.message.text,
                user_id=42,
                history=[],
                route_func=AsyncMock(),
            )

        self.assertTrue(handled)
        planner.assert_awaited_once()
        rewrite.assert_not_called()

    async def test_ai_rewrite_never_executes_directly_and_is_rerouted(self):
        update = _update("закинь в дела купить воду сегодня")
        context = _Context()
        route = AsyncMock(return_value=True)
        with (
            patch(
                "modules.assistant_flow.handle_unhandled_task_plan",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "modules.assistant_flow.interpret_unhandled_action",
                return_value="добавь задачу купить воду сегодня",
            ),
        ):
            handled = await recover_unhandled_action(
                update,
                context,
                update.message.text,
                user_id=42,
                history=[],
                route_func=route,
            )

        self.assertTrue(handled)
        route.assert_awaited_once_with(
            update,
            context,
            text="добавь задачу купить воду сегодня",
        )

    async def test_no_rewrite_leaves_message_for_conversational_ai(self):
        update = _update("почему я всё откладываю?")
        context = _Context()
        route = AsyncMock()
        with (
            patch(
                "modules.assistant_flow.handle_unhandled_task_plan",
                new=AsyncMock(return_value=False),
            ),
            patch("modules.assistant_flow.interpret_unhandled_action", return_value=None),
        ):
            handled = await recover_unhandled_action(
                update,
                context,
                update.message.text,
                user_id=42,
                history=[],
                route_func=route,
            )

        self.assertFalse(handled)
        route.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
