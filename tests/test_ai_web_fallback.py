from __future__ import annotations

import unittest
from unittest.mock import patch

import web_app
from core.db import get_or_create_google_user
from modules.ai_assistant import UNHANDLED_WEB_MESSAGE
from tests.web_test_support import web_test_app


class AIWebFallbackTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        web_app._user_state.clear()
        self.user_id = get_or_create_google_user(
            "ai-web-fallback",
            "ai-web-fallback@example.test",
            "AI Web",
        )
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def test_unhandled_chat_is_replaced_by_ai_answer(self):
        async def unhandled_route(update, context, text=None):
            return False

        with patch("web_app.route_text", side_effect=unhandled_route), patch(
            "modules.assistant_api.answer_unhandled",
            return_value="Тебе лучше назначать встречи после 10 утра.",
        ) as answer:
            response = self.client.post(
                "/api/chat",
                json={"message": "В какое время мне лучше назначать встречи?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["handled"])
        self.assertEqual(payload["replies"], ["Тебе лучше назначать встречи после 10 утра."])
        answer.assert_called_once_with(
            "В какое время мне лучше назначать встречи?",
            user_id=self.user_id,
        )

    def test_personal_time_question_reaches_ai_without_calendar_search(self):
        with patch("modules.calendar_user._list_events") as calendar_search, patch(
            "modules.assistant_api.answer_unhandled",
            return_value="Ты принимаешь таблетки в 23:00.",
        ) as answer:
            response = self.client.post(
                "/api/chat",
                json={"message": "Во сколько я принимаю таблетки?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["handled"])
        self.assertEqual(payload["replies"], ["Ты принимаешь таблетки в 23:00."])
        calendar_search.assert_not_called()
        answer.assert_called_once_with(
            "Во сколько я принимаю таблетки?",
            user_id=self.user_id,
        )

    def test_unavailable_ai_keeps_safe_router_fallback(self):
        async def unhandled_route(update, context, text=None):
            return False

        with patch("web_app.route_text", side_effect=unhandled_route), patch(
            "modules.assistant_api.answer_unhandled",
            return_value=None,
        ):
            response = self.client.post(
                "/api/chat",
                json={"message": "В какое время мне лучше назначать встречи?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["handled"])
        self.assertEqual(payload["replies"], [UNHANDLED_WEB_MESSAGE])


if __name__ == "__main__":
    unittest.main()
