from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from integrations import ai
from integrations import ai_files


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class AIFileTransportTests(unittest.TestCase):
    def setUp(self):
        ai._reset_token_cache_for_tests()
        self.settings = ai.AISettings(
            enabled=True,
            provider="gigachat",
            model="GigaChat-2",
            credentials="test-credentials",
            scope="GIGACHAT_API_PERS",
            base_url="https://api.giga.chat/v1",
            auth_url="https://auth.example/token",
            timeout_seconds=30,
            max_output_tokens=700,
            ca_bundle=None,
        )

    def tearDown(self):
        ai._reset_token_cache_for_tests()

    def test_upload_is_ephemeral_general_file(self):
        with patch("integrations.ai_files.load_ai_settings", return_value=self.settings), \
             patch("integrations.ai_files._ensure_gigachat_ca_bundle", return_value="/tmp/ca.pem"), \
             patch("integrations.ai_files._get_access_token", return_value="token"), \
             patch("integrations.ai_files.requests.post", return_value=_Response(200, {"id": "file-123"})) as post:
            file_id = ai_files.upload_file_bytes(b"pdf", "document.pdf", "application/pdf")
        self.assertEqual(file_id, "file-123")
        self.assertEqual(post.call_args.args[0], "https://api.giga.chat/v1/files")
        self.assertEqual(post.call_args.kwargs["data"], {"purpose": "general"})
        self.assertEqual(post.call_args.kwargs["files"]["file"][0], "document.pdf")
        self.assertEqual(post.call_args.kwargs["files"]["file"][2], "application/pdf")

    def test_completion_attaches_file_and_uses_pro_model(self):
        response = _Response(200, {"choices": [{"message": {"content": '{"events":[]}'}}]})
        with patch.dict(os.environ, {}, clear=True), \
             patch("integrations.ai_files.load_ai_settings", return_value=self.settings), \
             patch("integrations.ai_files._ensure_gigachat_ca_bundle", return_value="/tmp/ca.pem"), \
             patch("integrations.ai_files._get_access_token", return_value="token"), \
             patch("integrations.ai_files.requests.post", return_value=response) as post:
            answer = ai_files.complete_with_file("file-123", "Разбери билет")
        self.assertEqual(answer, '{"events":[]}')
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "GigaChat-2-Pro")
        self.assertEqual(payload["messages"][0]["attachments"], ["file-123"])
        self.assertEqual(payload["function_call"], "auto")

    def test_delete_uses_provider_delete_endpoint(self):
        with patch("integrations.ai_files.load_ai_settings", return_value=self.settings), \
             patch("integrations.ai_files._ensure_gigachat_ca_bundle", return_value="/tmp/ca.pem"), \
             patch("integrations.ai_files._get_access_token", return_value="token"), \
             patch("integrations.ai_files.requests.post", return_value=_Response(200, {})) as post:
            ai_files.delete_file("file-123")
        self.assertEqual(post.call_args.args[0], "https://api.giga.chat/v1/files/file-123/delete")

    def test_401_refreshes_token_once(self):
        responses = [_Response(401, {}), _Response(200, {"id": "file-456"})]
        with patch("integrations.ai_files.load_ai_settings", return_value=self.settings), \
             patch("integrations.ai_files._ensure_gigachat_ca_bundle", return_value="/tmp/ca.pem"), \
             patch("integrations.ai_files._get_access_token", side_effect=["old-token", "new-token"]) as token, \
             patch("integrations.ai_files._invalidate_token") as invalidate, \
             patch("integrations.ai_files.requests.post", side_effect=responses) as post:
            file_id = ai_files.upload_file_bytes(b"pdf", "document.pdf", "application/pdf")
        self.assertEqual(file_id, "file-456")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(token.call_count, 2)
        invalidate.assert_called_once_with(self.settings)
        self.assertEqual(post.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer new-token")


if __name__ == "__main__":
    unittest.main()
