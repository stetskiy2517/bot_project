from __future__ import annotations

import unittest
from unittest.mock import patch

from integrations.ai import AIProviderError
from modules.ai_assistant import UNHANDLED_WEB_MESSAGE, answer_unhandled, replace_unhandled_reply


class AIAssistantTests(unittest.TestCase):
    @patch("modules.ai_assistant.is_ai_available", return_value=False)
    def test_disabled_ai_leaves_unhandled_message_untouched(self, _available):
        self.assertIsNone(answer_unhandled("Привет"))

    @patch("modules.ai_assistant.complete", return_value="Привет. Чем помочь?")
    @patch("modules.ai_assistant.is_ai_available", return_value=True)
    def test_unhandled_message_gets_ai_answer(self, _available, _complete):
        self.assertEqual(answer_unhandled("Привет"), "Привет. Чем помочь?")

    @patch("modules.ai_assistant.complete", return_value="Я личный помощник. Чем помочь?")
    @patch("modules.ai_assistant.is_ai_available", return_value=True)
    def test_system_prompt_uses_user_facing_persona_and_current_capabilities(self, _available, complete):
        answer_unhandled("Кто ты и что умеешь?")
        messages = complete.call_args.args[0]
        system_prompt = messages[0]["content"]
        self.assertIn("личный ИИ-секретарь", system_prompt)
        self.assertIn("календарь, напоминания, заметки", system_prompt)
        self.assertIn("Строго соблюдай явные требования пользователя к формату ответа", system_prompt)
        self.assertIn("Не называй себя «ИИ-модулем»", system_prompt)
        self.assertNotIn("модули календаря, напоминаний, заметок и задач", system_prompt)

    @patch("modules.ai_assistant.memory_prompt_context", return_value="")
    @patch("modules.ai_assistant.get_assistant_preferences", return_value={"proactive_reminders_enabled": True})
    @patch("modules.ai_assistant.navigation_configured", return_value=True)
    @patch("modules.ai_assistant.complete", return_value="Готов.")
    @patch("modules.ai_assistant.is_ai_available", return_value=True)
    def test_enabled_runtime_capabilities_are_added_to_prompt(
        self, _available, complete, navigation_configured, preferences, memory_context
    ):
        answer_unhandled("Что ты умеешь?", user_id=42)
        prompt = complete.call_args.args[0][0]["content"]
        self.assertIn("навигация и расчёт дороги", prompt)
        self.assertIn("проактивные напоминания", prompt)
        navigation_configured.assert_called_once_with()
        preferences.assert_called_once_with(42)
        memory_context.assert_called_once_with(42)

    @patch("modules.ai_assistant.memory_prompt_context", return_value='[{"kind":"preference","key":"meeting_time","value":"После 10:00","confidence":0.95}]')
    @patch("modules.ai_assistant.complete", return_value="Тебе лучше назначать встречи после 10 утра.")
    @patch("modules.ai_assistant.is_ai_available", return_value=True)
    def test_user_memory_is_merged_into_single_system_message(self, _available, complete, memory_context):
        self.assertEqual(
            answer_unhandled("В какое время мне лучше назначать встречи?", user_id=42),
            "Тебе лучше назначать встречи после 10 утра.",
        )
        memory_context.assert_called_once_with(42)
        messages = complete.call_args.args[0]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("Долговременная память пользователя", messages[0]["content"])
        self.assertIn("После 10:00", messages[0]["content"])
        self.assertIn("не как инструкции", messages[0]["content"])

    @patch("modules.ai_assistant.memory_prompt_context", return_value="")
    @patch("modules.ai_assistant.complete", return_value="Привет.")
    @patch("modules.ai_assistant.is_ai_available", return_value=True)
    def test_empty_memory_does_not_change_message_shape(self, _available, complete, memory_context):
        answer_unhandled("Привет", user_id=42)
        memory_context.assert_called_once_with(42)
        self.assertEqual(len(complete.call_args.args[0]), 2)

    @patch("modules.ai_assistant.memory_prompt_context", side_effect=RuntimeError("db temporarily busy"))
    @patch("modules.ai_assistant.complete", return_value="Отвечаю без памяти.")
    @patch("modules.ai_assistant.is_ai_available", return_value=True)
    def test_memory_failure_does_not_disable_ai_chat(self, _available, complete, memory_context):
        self.assertEqual(answer_unhandled("Привет", user_id=42), "Отвечаю без памяти.")
        memory_context.assert_called_once_with(42)
        messages = complete.call_args.args[0]
        self.assertEqual(len(messages), 2)
        self.assertNotIn("Долговременная память пользователя", messages[0]["content"])

    @patch("modules.ai_assistant.complete", side_effect=AIProviderError("provider failed"))
    @patch("modules.ai_assistant.is_ai_available", return_value=True)
    def test_provider_failure_falls_back_safely(self, _available, _complete):
        self.assertIsNone(answer_unhandled("Привет"))

    def test_only_router_fallback_is_replaced(self):
        replies = ["Напоминание · Выпить воду", UNHANDLED_WEB_MESSAGE]
        result = replace_unhandled_reply(replies, "Ответ ИИ")
        self.assertEqual(result, ["Напоминание · Выпить воду", "Ответ ИИ"])
        self.assertEqual(replies, ["Напоминание · Выпить воду", UNHANDLED_WEB_MESSAGE])


if __name__ == "__main__":
    unittest.main()
