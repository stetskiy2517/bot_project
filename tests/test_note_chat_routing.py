from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from modules.notes import NOTE_CREATE, detect_bare_note, detect_note_intent
from modules.router import route_text


class NoteChatRoutingTests(unittest.TestCase):
    def test_reported_greeting_prompt_is_not_a_note(self):
        text = "Привет. Расскажи в двух предложениях, кто ты и чем можешь мне помочь."
        self.assertIsNone(detect_bare_note(text))
        self.assertIsNone(detect_note_intent(text))

    def test_other_conversational_title_body_phrases_are_not_notes(self):
        phrases = [
            "Добрый вечер. Помоги выбрать сериал",
            "Слушай. Объясни, чем ты можешь помочь",
            "Вопрос. Подскажи, как лучше спланировать неделю",
            "Привет. Сравни два варианта",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIsNone(detect_bare_note(phrase))
                self.assertIsNone(detect_note_intent(phrase))

    def test_real_bare_notes_still_work(self):
        cases = {
            "Список покупок. Бананы, масло, хлеб": ("Список покупок", "Бананы, масло, хлеб"),
            "Проект Альфа. Иван ждёт смету": ("Проект Альфа", "Иван ждёт смету"),
            "Идеи для отпуска. Испания, машина, отель у моря": (
                "Идеи для отпуска",
                "Испания, машина, отель у моря",
            ),
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(detect_bare_note(phrase), expected)
                self.assertEqual(detect_note_intent(phrase), NOTE_CREATE)


class NoteChatRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_reported_phrase_reaches_unhandled_fallback_path(self):
        text = "Привет. Расскажи в двух предложениях, кто ты и чем можешь мне помочь."
        update = SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=987654321),
        )
        context = SimpleNamespace(user_data={})

        handled = await route_text(update, context, text=text)

        self.assertFalse(handled)
        update.message.reply_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
