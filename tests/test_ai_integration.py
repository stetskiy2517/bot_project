from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from integrations import ai


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


class AIIntegrationTests(unittest.TestCase):
    def setUp(self):
        ai._reset_token_cache_for_tests()

    def tearDown(self):
        ai._reset_token_cache_for_tests()

    @patch("integrations.ai.DOTENV_PATH")
    @patch("integrations.ai.dotenv_values", return_value={})
    def test_missing_credentials_keep_ai_unconfigured(self, _dotenv_values, dotenv_path):
        dotenv_path.exists.return_value = True
        with patch.dict(os.environ, {}, clear=True):
            settings = ai.load_ai_settings()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.provider, "gigachat")
        self.assertFalse(settings.configured)

    @patch("integrations.ai.DOTENV_PATH")
    @patch("integrations.ai.dotenv_values")
    def test_credentials_are_read_from_dotenv_on_each_call(self, dotenv_values, dotenv_path):
        dotenv_path.exists.return_value = True
        dotenv_values.return_value = {"GIGACHAT_CREDENTIALS": "fresh-key"}
        with patch.dict(os.environ, {"GIGACHAT_CREDENTIALS": ""}, clear=True):
            first = ai.load_ai_settings()
            dotenv_values.return_value = {"GIGACHAT_CREDENTIALS": "updated-key"}
            second = ai.load_ai_settings()
        self.assertEqual(first.credentials, "fresh-key")
        self.assertEqual(second.credentials, "updated-key")
        self.assertTrue(second.configured)

    def test_access_token_is_reused_between_completions(self):
        settings = ai.AISettings(
            enabled=True,
            provider="gigachat",
            model="GigaChat-2-Lite",
            credentials="test-credentials",
            scope="GIGACHAT_API_PERS",
            base_url="https://api.giga.chat/v1",
            auth_url="https://auth.example/token",
            timeout_seconds=30,
            max_output_tokens=128,
            ca_bundle=None,
        )
        responses = [
            _Response(200, {"access_token": "token-1", "expires_at": int((time.time() + 1800) * 1000)}),
            _Response(200, {"choices": [{"message": {"content": "Первый ответ"}}]}),
            _Response(200, {"choices": [{"message": {"content": "Второй ответ"}}]}),
        ]
        with patch("integrations.ai.load_ai_settings", return_value=settings), \
             patch("integrations.ai._ensure_gigachat_ca_bundle", return_value="/tmp/ca.pem"), \
             patch("integrations.ai.requests.post", side_effect=responses) as post:
            first = ai.complete([{"role": "user", "content": "Привет"}])
            second = ai.complete([{"role": "user", "content": "Ещё раз"}])
        self.assertEqual(first, "Первый ответ")
        self.assertEqual(second, "Второй ответ")
        self.assertEqual(post.call_count, 3)
        self.assertEqual(post.call_args_list[0].args[0], "https://auth.example/token")
        self.assertTrue(post.call_args_list[1].args[0].endswith("/chat/completions"))
        self.assertTrue(post.call_args_list[2].args[0].endswith("/chat/completions"))

    def test_structured_completion_returns_object(self):
        settings = ai.AISettings(
            enabled=True,
            provider="gigachat",
            model="GigaChat-2-Lite",
            credentials="test-credentials",
            scope="GIGACHAT_API_PERS",
            base_url="https://api.giga.chat/v1",
            auth_url="https://auth.example/token",
            timeout_seconds=30,
            max_output_tokens=128,
            ca_bundle=None,
        )
        schema = {
            "type": "object",
            "properties": {"intent": {"type": "string"}},
            "required": ["intent"],
            "additionalProperties": False,
        }
        with patch("integrations.ai.load_ai_settings", return_value=settings), \
             patch("integrations.ai._gigachat_completion", return_value='{"intent":"chat"}') as completion:
            result = ai.complete_structured([{"role": "user", "content": "Привет"}], schema)
        self.assertEqual(result, {"intent": "chat"})
        response_format = completion.call_args.kwargs["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["strict"])
        self.assertEqual(response_format["schema"], schema)


if __name__ == "__main__":
    unittest.main()
