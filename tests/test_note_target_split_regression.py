from __future__ import annotations

import unittest
from unittest.mock import patch

from modules.note_conversation import resolve_named_note_append


NOTE = {
    "note_id": 41,
    "user_id": 1,
    "title": "Список покупок",
    "text": "Бананы\nмасло сливочное",
    "created_at": "2026-09-13T09:00:00+00:00",
    "updated_at": "2026-09-13T09:00:00+00:00",
}


class NoteTargetSplitRegressionTests(unittest.TestCase):
    def test_exact_screenshot_phrase_splits_title_from_all_items(self):
        def search(_user_id, query, *, limit):
            return [NOTE] if query.lower() == "список покупок" else []

        text = "Добавь в список покупок мясо, птицу, рыбу, фрукты, помидор, печенье"
        with patch("modules.note_conversation.search_notes", side_effect=search):
            resolved = resolve_named_note_append(1, text)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.query, "список покупок")
        self.assertEqual(
            resolved.addition,
            "мясо, птицу, рыбу, фрукты, помидор, печенье",
        )
        self.assertEqual([item["note_id"] for item in resolved.matches], [41])

    def test_note_title_is_not_extended_by_first_item(self):
        queries = []

        def search(_user_id, query, *, limit):
            queries.append(query.lower())
            return [NOTE] if query.lower() == "список покупок" else []

        with patch("modules.note_conversation.search_notes", side_effect=search):
            resolved = resolve_named_note_append(
                1,
                "Добавь в список покупок мясо курицу рыбу",
            )

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.query, "список покупок")
        self.assertEqual(resolved.addition, "мясо курицу рыбу")
        self.assertIn("список покупок", queries)


if __name__ == "__main__":
    unittest.main()
