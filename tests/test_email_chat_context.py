import json
import unittest
from unittest.mock import patch

from flask import Flask, Response, session

from core.chat_context import clear_chat_context, recent_chat_messages
from modules.email_api import email_response_hooks


class EmailChatContextTests(unittest.TestCase):
    def setUp(self):
        clear_chat_context()
        self.app = Flask(__name__)
        self.app.secret_key = "test-secret"

    def tearDown(self):
        clear_chat_context()

    def test_successful_email_chat_answer_is_available_to_follow_up_ai(self):
        incoming = "Покажи непрочитанные письма"
        answer = "Есть два непрочитанных письма от клиента."
        response = Response(
            json.dumps({"handled": False, "replies": ["Не понял команду."]}),
            mimetype="application/json",
        )

        with self.app.test_request_context("/api/chat", method="POST", json={"message": incoming}):
            session["user_id"] = 42
            with (
                patch("modules.email_api.detect_email_intent", return_value=True),
                patch("modules.email_api.answer_email_chat_request_details", return_value=(answer, None)),
            ):
                result = email_response_hooks(response)

        payload = json.loads(result.get_data(as_text=True))
        self.assertTrue(payload["handled"])
        self.assertEqual(payload["replies"], [answer])
        self.assertEqual(
            recent_chat_messages(42),
            [
                {"role": "user", "content": incoming},
                {"role": "assistant", "content": answer},
            ],
        )

    def test_failed_email_query_is_not_saved_as_factual_context(self):
        incoming = "Проверь почту"
        response = Response(
            json.dumps({"handled": False, "replies": ["Не понял команду."]}),
            mimetype="application/json",
        )

        with self.app.test_request_context("/api/chat", method="POST", json={"message": incoming}):
            session["user_id"] = 42
            with (
                patch("modules.email_api.detect_email_intent", return_value=True),
                patch("modules.email_api.answer_email_chat_request_details", side_effect=RuntimeError("mail unavailable")),
            ):
                result = email_response_hooks(response)

        payload = json.loads(result.get_data(as_text=True))
        self.assertTrue(payload["handled"])
        self.assertIn("Не удалось прочитать почту", payload["replies"][0])
        self.assertEqual(recent_chat_messages(42), [])


if __name__ == "__main__":
    unittest.main()
