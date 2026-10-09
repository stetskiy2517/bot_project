from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import web_app
from core.conversation_context import clear_user_state
from core.chat_context import clear_chat_context
from core.db import get_or_create_google_user
from modules.ai_assistant import UNHANDLED_WEB_MESSAGE, UNHANDLED_WEB_MESSAGE_EN
from tests.web_test_support import web_test_app


class AIWebFallbackTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        clear_user_state()
        self.user_id = get_or_create_google_user(
            "ai-web-fallback",
            "ai-web-fallback@example.test",
            "AI Web",
        )
        clear_chat_context(self.user_id)
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def test_task_list_planner_runs_before_conversational_ai(self):
        async def unhandled_route(update, context, text=None):
            return False

        async def task_plan(update, context, text, history=None):
            update.message.replies.append("Вижу 3 дела. На какой день распланировать их по календарю?")
            return True

        with patch("web_app.route_text", side_effect=unhandled_route), patch(
            "web_app.handle_unhandled_task_plan",
            side_effect=task_plan,
        ) as planner, patch(
            "web_app.interpret_unhandled_action",
        ) as rewrite, patch(
            "web_app.answer_unhandled",
        ) as answer:
            response = self.client.post(
                "/api/chat",
                json={"message": "Отправить паспорт Артему, позвонить Ольхову, сделать домашнее задание"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["handled"])
        self.assertEqual(
            payload["replies"],
            ["Вижу 3 дела. На какой день распланировать их по календарю?"],
        )
        planner.assert_called_once()
        rewrite.assert_not_called()
        answer.assert_not_called()

    def test_ai_action_rewrite_is_rerouted_through_deterministic_router(self):
        calls = []

        async def route(update, context, text=None):
            calls.append(text)
            if text == "добавь задачу постирать белье сегодня":
                update.message.replies.append("Задача добавлена · «Постирать белье» · сегодня")
                return True
            return False

        with patch("web_app.route_text", side_effect=route), patch(
            "web_app.interpret_unhandled_action",
            return_value="добавь задачу постирать белье сегодня",
        ) as rewrite, patch(
            "web_app.answer_unhandled",
        ) as answer:
            response = self.client.post(
                "/api/chat",
                json={"message": "Закинь в дела постирать белье на сегодня"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["handled"])
        self.assertEqual(payload["replies"], ["Задача добавлена · «Постирать белье» · сегодня"])
        self.assertEqual(calls, [
            "Закинь в дела постирать белье на сегодня",
            "добавь задачу постирать белье сегодня",
        ])
        rewrite.assert_called_once_with(
            "Закинь в дела постирать белье на сегодня",
            user_id=self.user_id,
        )
        answer.assert_not_called()

    def test_ai_action_rewrite_none_falls_back_to_conversation_ai(self):
        async def unhandled_route(update, context, text=None):
            return False

        with patch("web_app.route_text", side_effect=unhandled_route), patch(
            "web_app.interpret_unhandled_action",
            return_value=None,
        ), patch(
            "web_app.answer_unhandled",
            return_value="Могу помочь разобраться.",
        ) as answer:
            response = self.client.post(
                "/api/chat",
                json={"message": "Почему я всё откладываю?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["handled"])
        self.assertEqual(payload["replies"], ["Могу помочь разобраться."])
        answer.assert_called_once()

    def test_unhandled_chat_is_replaced_by_ai_answer(self):
        async def unhandled_route(update, context, text=None):
            return False

        with patch("web_app.route_text", side_effect=unhandled_route), patch(
            "web_app.answer_unhandled",
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
            history=[],
        )

    def test_english_unhandled_chat_reaches_ai_after_bilingual_routing(self):
        with patch(
            "web_app.answer_unhandled",
            return_value="You are free after 4 PM.",
        ) as answer:
            response = self.client.post(
                "/api/chat",
                json={"message": "Could you explain that a bit more?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["handled"])
        self.assertEqual(payload["replies"], ["You are free after 4 PM."])
        answer.assert_called_once_with(
            "Could you explain that a bit more?",
            user_id=self.user_id,
            history=[],
        )

    def test_personal_time_question_reaches_ai_without_calendar_search(self):
        with patch("modules.calendar_user._list_events") as calendar_search, patch(
            "web_app.answer_unhandled",
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
            history=[],
        )

    def test_followup_receives_previous_ai_exchange(self):
        calls = []

        def answer(text, *, user_id=None, history=None):
            calls.append((text, user_id, list(history or [])))
            if text == "Нет":
                return "Понял, предыдущее время неверно. Во сколько ты принимаешь таблетки?"
            return "Ты принимаешь таблетки в 23:00."

        with patch("web_app.answer_unhandled", side_effect=answer):
            first = self.client.post("/api/chat", json={"message": "Во сколько я принимаю таблетки?"})
            second = self.client.post("/api/chat", json={"message": "Нет"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][2], [])
        self.assertEqual(
            calls[1][2],
            [
                {"role": "user", "content": "Во сколько я принимаю таблетки?"},
                {"role": "assistant", "content": "Ты принимаешь таблетки в 23:00."},
            ],
        )
        self.assertEqual(
            second.get_json()["replies"],
            ["Понял, предыдущее время неверно. Во сколько ты принимаешь таблетки?"],
        )

    def test_first_person_correction_reaches_ai_instead_of_calendar_creation(self):
        with patch(
            "web_app.answer_unhandled",
            return_value="Понял: таблетки в 23:00.",
        ):
            response = self.client.post(
                "/api/chat",
                json={"message": "Я пью таблетки в 23:00"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["handled"])
        self.assertEqual(payload["replies"], ["Понял: таблетки в 23:00."])

    def test_ai_chat_answer_is_persisted_in_idempotent_request_receipt(self):
        async def unhandled_route(update, context, text=None):
            return False

        request_id = "1791530000000-0123456789abcdef0123456789abcdef"
        with self.client.session_transaction() as stored:
            stored.setdefault("csrf_token", "test-csrf-token")
            csrf = stored["csrf_token"]

        headers = {
            "X-CSRF-Token": csrf,
            "X-Request-ID": request_id,
        }
        with patch("web_app.route_text", side_effect=unhandled_route), patch(
            "web_app.handle_unhandled_task_plan",
            new=AsyncMock(return_value=False),
        ), patch(
            "web_app.interpret_unhandled_action",
            return_value=None,
        ), patch(
            "web_app.answer_unhandled",
            return_value="Сначала выбери одну небольшую задачу.",
        ) as answer:
            first = self.client.post(
                "/api/chat",
                json={"message": "Почему я всё откладываю?"},
                headers=headers,
            )
            second = self.client.post(
                "/api/chat",
                json={"message": "Почему я всё откладываю?"},
                headers=headers,
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["replies"], ["Сначала выбери одну небольшую задачу."])
        self.assertEqual(second.get_json()["replies"], ["Сначала выбери одну небольшую задачу."])
        self.assertEqual(answer.call_count, 1)
        self.assertEqual(second.headers.get("X-Request-Replayed"), "true")

    def test_unavailable_ai_keeps_safe_router_fallback(self):
        async def unhandled_route(update, context, text=None):
            return False

        with patch("web_app.route_text", side_effect=unhandled_route), patch(
            "web_app.answer_unhandled",
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

    def test_unavailable_ai_keeps_localized_english_fallback(self):
        async def unhandled_route(update, context, text=None):
            return False

        with patch("web_app.route_text", side_effect=unhandled_route), patch(
            "web_app.answer_unhandled",
            return_value=None,
        ):
            response = self.client.post(
                "/api/chat",
                json={"message": "Could you explain that differently?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["handled"])
        self.assertEqual(payload["replies"], [UNHANDLED_WEB_MESSAGE_EN])


if __name__ == "__main__":
    unittest.main()
