from __future__ import annotations

import imaplib
import unittest
import uuid
from unittest.mock import patch

from core.db import conn, db_lock, get_or_create_google_user
from core.email_store import delete_email_account, get_email_account, list_email_accounts, save_email_account
from integrations.email_imap import EmailAuthenticationError, _connect, _message_payload, list_messages
from modules.email import _query_from_text, answer_email_query, detect_email_intent


class EmailAssistantTests(unittest.TestCase):
    def setUp(self):
        token = uuid.uuid4().hex
        self.user_id = get_or_create_google_user(
            f"email-test-{token}",
            f"email-test-{token}@example.test",
            "Email Test",
        )

    def tearDown(self):
        with db_lock:
            for table in ("email_oauth_states", "email_accounts", "users", "google_accounts"):
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if "user_id" in columns:
                    conn.execute(f"DELETE FROM {table} WHERE user_id=?", (self.user_id,))
            conn.commit()

    def test_detects_mail_commands(self):
        self.assertTrue(detect_email_intent("Что нового в почте?"))
        self.assertTrue(detect_email_intent("Найди письмо от Иванова"))
        self.assertTrue(detect_email_intent("Покажи непрочитанные входящие"))
        self.assertFalse(detect_email_intent("Что у меня завтра в календаре?"))

    def test_search_query_accepts_natural_phrasing_with_and_without_preposition(self):
        self.assertEqual(_query_from_text("Найди письмо от ресо"), "ресо")
        self.assertEqual(_query_from_text("Найди письмо от вкусно и точка"), "вкусно и точка")
        self.assertEqual(_query_from_text("Найди письмо Ozon"), "Ozon")
        self.assertEqual(_query_from_text("Поищи письмо Samsung"), "Samsung")
        self.assertIsNone(_query_from_text("Покажи непрочитанные входящие"))

    def test_credentials_are_encrypted_at_rest(self):
        secret = "app-password-super-secret"
        account = save_email_account(
            self.user_id,
            "yandex",
            "owner@yandex.ru",
            {"app_password": secret},
            display_name="Яндекс",
        )
        with db_lock:
            row = conn.execute(
                "SELECT credential_encrypted FROM email_accounts WHERE account_id=?",
                (account["account_id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotIn(secret, row[0])
        loaded = get_email_account(self.user_id, account["account_id"], with_credentials=True)
        self.assertEqual(loaded["credentials"]["app_password"], secret)

    def test_account_is_scoped_to_user(self):
        account = save_email_account(
            self.user_id,
            "mailru",
            "owner@mail.ru",
            {"app_password": "secret"},
        )
        self.assertEqual(len(list_email_accounts(self.user_id)), 1)
        self.assertIsNone(get_email_account(self.user_id + 999999, account["account_id"], with_credentials=True))
        self.assertTrue(delete_email_account(self.user_id, account["account_id"]))
        self.assertEqual(list_email_accounts(self.user_id), [])

    def test_rfc_headers_and_plain_text_are_decoded(self):
        raw = (
            b"From: =?utf-8?b?0JjQstCw0L0=?= <ivan@example.com>\r\n"
            b"Subject: =?utf-8?b?0KLQtdGB0YI=?=\r\n"
            b"Date: Tue, 15 Sep 2026 09:30:00 +0300\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            + "Привет, это письмо для теста.".encode("utf-8")
        )
        payload = _message_payload(raw)
        self.assertIn("Иван", payload["from"])
        self.assertEqual(payload["subject"], "Тест")
        self.assertIn("письмо для теста", payload["preview"])

    @patch("integrations.email_imap.imaplib.IMAP4_SSL")
    def test_imap_connection_strips_pasted_whitespace(self, imap_ssl):
        client = imap_ssl.return_value
        result = _connect("yandex", " owner@yandex.ru ", " app-secret \n")
        self.assertIs(result, client)
        client.login.assert_called_once_with("owner@yandex.ru", "app-secret")

    @patch("integrations.email_imap.imaplib.IMAP4_SSL")
    def test_yandex_auth_error_explains_activation_delay_and_imap(self, imap_ssl):
        client = imap_ssl.return_value
        client.login.side_effect = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        with self.assertRaises(EmailAuthenticationError) as caught:
            _connect("yandex", "owner@yandex.ru", "app-secret")
        message = str(caught.exception)
        self.assertIn("2–3 часа", message)
        self.assertIn("IMAP", message)
        self.assertIn("Пароли приложений", message)

    @patch("integrations.email_imap._connect")
    def test_imap_search_uses_server_search_instead_of_last_50_messages(self, connect):
        client = connect.return_value
        client.select.return_value = ("OK", [b""])
        client.search.return_value = ("OK", [b"1"])
        raw = (
            b"From: Ozon <news@ozon.ru>\r\n"
            b"Subject: Order archive\r\n"
            b"Date: Tue, 01 Sep 2026 09:30:00 +0300\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Old message that is still searchable."
        )
        client.fetch.return_value = ("OK", [(b"1 (RFC822 {100}", raw), b")"])

        messages = list_messages("yandex", "owner@yandex.ru", "secret", query="Ozon", limit=10)

        self.assertEqual(len(messages), 1)
        self.assertIn("Ozon", messages[0]["from"])
        search_args = client.search.call_args.args
        self.assertEqual(search_args[0], "UTF-8")
        self.assertIn("FROM", search_args)
        self.assertIn("SUBJECT", search_args)
        self.assertIn("BODY", search_args)

    @patch("modules.email._read_account")
    def test_direct_search_passes_query_to_mail_provider(self, read_account):
        save_email_account(self.user_id, "yandex", "one@yandex.ru", {"app_password": "one"}, display_name="Яндекс")
        read_account.return_value = []

        answer_email_query(self.user_id, "Найди письмо Ozon")

        self.assertEqual(read_account.call_args.kwargs["query"], "Ozon")

    @patch("modules.email._read_account")
    def test_query_combines_connected_accounts(self, read_account):
        save_email_account(self.user_id, "yandex", "one@yandex.ru", {"app_password": "one"}, display_name="Яндекс")
        save_email_account(self.user_id, "mailru", "two@mail.ru", {"app_password": "two"}, display_name="Mail.ru")
        read_account.side_effect = [
            [{"from": "Иван <ivan@example.com>", "subject": "Смета", "date": "", "preview": "Жду ответ"}],
            [{"from": "Пётр <petr@example.com>", "subject": "Встреча", "date": "", "preview": "На пятницу"}],
        ]
        answer = answer_email_query(self.user_id, "Что нового в почте?")
        self.assertIn("Смета", answer)
        self.assertIn("Встреча", answer)
        self.assertEqual(read_account.call_count, 2)

    def test_without_accounts_gives_connection_hint(self):
        self.assertIn("Почта ещё не подключена", answer_email_query(self.user_id, "Что в почте?"))


if __name__ == "__main__":
    unittest.main()
