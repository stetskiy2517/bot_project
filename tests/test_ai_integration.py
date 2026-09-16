from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from integrations import ai


class _Response:
    def __init__(self, status_code: int, payload: dict, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.content = b""
        self.headers = headers or {}

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
        self.assertEqual(settings.model, "GigaChat-2")
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

    def _settings(self, scope="GIGACHAT_API_PERS"):
        return ai.AISettings(
            enabled=True,
            provider="gigachat",
            model="GigaChat-2",
            credentials="test-credentials",
            scope=scope,
            base_url="https://api.giga.chat/v1",
            auth_url="https://auth.example/token",
            timeout_seconds=30,
            max_output_tokens=128,
            ca_bundle=None,
        )

    def test_access_token_is_reused_between_completions(self):
        settings = self._settings()
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

    def test_rate_limit_is_retried_then_recovers(self):
        settings = self._settings()
        responses = [
            _Response(200, {"access_token": "token-1", "expires_at": int((time.time() + 1800) * 1000)}),
            _Response(429, {"status": 429}, {"Retry-After": "0"}),
            _Response(200, {"choices": [{"message": {"content": "После повтора"}}]}),
        ]
        with patch("integrations.ai.load_ai_settings", return_value=settings), \
             patch("integrations.ai._ensure_gigachat_ca_bundle", return_value="/tmp/ca.pem"), \
             patch("integrations.ai.requests.post", side_effect=responses) as post, \
             patch("integrations.ai.time.sleep") as sleep:
            answer = ai.complete([{"role": "user", "content": "Привет"}])
            status = ai.get_ai_status()
        self.assertEqual(answer, "После повтора")
        self.assertEqual(post.call_count, 3)
        sleep.assert_called_once_with(0.0)
        self.assertEqual(status["state"], "healthy")
        self.assertIsNone(status["last_error"])

    def test_repeated_rate_limit_raises_typed_error_and_marks_degraded(self):
        settings = self._settings()
        responses = [
            _Response(200, {"access_token": "token-1", "expires_at": int((time.time() + 1800) * 1000)}),
            _Response(429, {"status": 429}, {"Retry-After": "1"}),
            _Response(429, {"status": 429}, {"Retry-After": "1"}),
            _Response(429, {"status": 429}, {"Retry-After": "1"}),
        ]
        with patch("integrations.ai.load_ai_settings", return_value=settings), \
             patch("integrations.ai._ensure_gigachat_ca_bundle", return_value="/tmp/ca.pem"), \
             patch("integrations.ai.requests.post", side_effect=responses), \
             patch("integrations.ai.time.sleep") as sleep:
            with self.assertRaises(ai.AIRateLimitError) as error:
                ai.complete([{"role": "user", "content": "Привет"}])
            status = ai.get_ai_status()
        self.assertEqual(error.exception.retry_after, 1.0)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(status["state"], "degraded")
        self.assertEqual(status["last_error"], "rate_limited")
        self.assertEqual(status["retry_after_seconds"], 1.0)

    def test_transient_server_error_is_retried(self):
        settings = self._settings(scope="GIGACHAT_API_CORP")
        responses = [
            _Response(200, {"access_token": "token-1", "expires_at": int((time.time() + 1800) * 1000)}),
            _Response(503, {"status": 503}),
            _Response(200, {"choices": [{"message": {"content": "OK"}}]}),
        ]
        with patch("integrations.ai.load_ai_settings", return_value=settings), \
             patch("integrations.ai._ensure_gigachat_ca_bundle", return_value="/tmp/ca.pem"), \
             patch("integrations.ai.requests.post", side_effect=responses), \
             patch("integrations.ai.time.sleep"):
            self.assertEqual(ai.complete([{"role": "user", "content": "Привет"}]), "OK")

    def test_personal_scope_skips_unavailable_structured_output(self):
        settings = self._settings()
        schema = {
            "type": "object",
            "properties": {"intent": {"type": "string"}},
            "required": ["intent"],
            "additionalProperties": False,
        }
        with patch("integrations.ai.load_ai_settings", return_value=settings), \
             patch("integrations.ai._gigachat_completion") as completion:
            with self.assertRaises(ai.AIProviderError) as error:
                ai.complete_structured([{"role": "user", "content": "Привет"}], schema)
        self.assertIn("personal scope", str(error.exception))
        completion.assert_not_called()

    def test_structured_completion_returns_object_for_commercial_scope(self):
        settings = self._settings(scope="GIGACHAT_API_CORP")
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
