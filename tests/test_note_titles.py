from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.note_store import create_note, delete_note, derive_note_title, search_notes
from modules.notes import _format_line, _split_note_payload, create_note_from_text


class NoteTitleParsingTests(unittest.TestCase):
    def test_derive_short_title_from_plain_text(self):
        self.assertEqual(derive_note_title("Иван ждёт смету"), "Иван ждёт смету")
        self.assertEqual(
            derive_note_title("Иван ждёт смету и просит отправить расчёт до пятницы"),
            "Иван ждёт смету и просит отправить",
        )
        self.assertEqual(
            derive_note_title("Список покупок. Бананы, масло сливочное, масло оливковое"),
            "Список покупок",
        )

    def test_explicit_title_is_split_from_body(self):
        self.assertEqual(
            _split_note_payload("Проект Альфа: Иван ждёт смету"),
            ("Проект Альфа", "Иван ждёт смету"),
        )
        self.assertEqual(
            _split_note_payload("Ремонт кухни — купить плитку и краску"),
            ("Ремонт кухни", "купить плитку и краску"),
        )

    def test_time_colon_is_not_mistaken_for_title_separator(self):
        self.assertEqual(
            _split_note_payload("врач завтра в 15:00 взять результаты анализов"),
            (None, "врач завтра в 15:00 взять результаты анализов"),
        )

    def test_old_note_without_title_still_formats(self):
        note = {
            "note_id": 1,
            "text": "Старая заметка без отдельного названия",
            "created_at": "2026-09-13T09:00:00+00:00",
            "updated_at": "2026-09-13T09:00:00+00:00",
        }
        line = _format_line(note, "Europe/Moscow")
        self.assertIn("Старая заметка без отдельного названия", line)


class NoteTitleStoreTests(unittest.TestCase):
    USER_ID = 987654321

    def tearDown(self):
        for note in search_notes(self.USER_ID, "Альфа", limit=50):
            delete_note(self.USER_ID, note["note_id"])

    def test_create_and_find_note_by_title(self):
        note = create_note(
            self.USER_ID,
            "Иван ждёт смету",
            title="Проект Альфа",
        )
        self.assertEqual(note["title"], "Проект Альфа")
        self.assertEqual(note["text"], "Иван ждёт смету")

        matches = search_notes(self.USER_ID, "проекту Альфа", limit=10)
        self.assertTrue(matches)
        self.assertEqual(matches[0]["note_id"], note["note_id"])


class NoteTitleCommandTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _update(text: str):
        return SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=1),
        )

    @staticmethod
    def _context():
        return SimpleNamespace(user_data={})

    async def test_create_command_passes_explicit_title_to_store(self):
        update = self._update("запиши заметку Проект Альфа: Иван ждёт смету")
        context = self._context()
        stored = {
            "note_id": 7,
            "title": "Проект Альфа",
            "text": "Иван ждёт смету",
            "created_at": "2026-09-13T09:00:00+00:00",
            "updated_at": "2026-09-13T09:00:00+00:00",
        }
        with patch("modules.notes.create_note", return_value=stored) as create:
            handled = await create_note_from_text(update, context, update.message.text)

        self.assertTrue(handled)
        create.assert_called_once_with(1, "Иван ждёт смету", title="Проект Альфа")
        reply = update.message.reply_text.await_args.args[0]
        self.assertIn("Проект Альфа", reply)
        self.assertIn("Иван ждёт смету", reply)

    async def test_create_without_explicit_title_keeps_zero_friction_flow(self):
        update = self._update("запиши заметку купить фильтр для воды")
        context = self._context()
        stored = {
            "note_id": 8,
            "title": "купить фильтр для воды",
            "text": "купить фильтр для воды",
            "created_at": "2026-09-13T09:00:00+00:00",
            "updated_at": "2026-09-13T09:00:00+00:00",
        }
        with patch("modules.notes.create_note", return_value=stored) as create:
            handled = await create_note_from_text(update, context, update.message.text)

        self.assertTrue(handled)
        create.assert_called_once_with(1, "купить фильтр для воды", title=None)
        self.assertNotIn("smart_planner_pending", context.user_data)


if __name__ == "__main__":
    unittest.main()
