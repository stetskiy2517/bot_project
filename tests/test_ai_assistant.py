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
