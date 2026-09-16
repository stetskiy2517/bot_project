import unittest

from core.conversation_policy import (
    is_clear_new_command,
    is_declarative_statement,
    should_resume_pending,
)


class ConversationPolicyTests(unittest.TestCase):
    def test_routine_and_medication_facts_are_declarative(self):
        self.assertTrue(is_declarative_statement("Я принимаю таблетки завтра в 23:00"))
        self.assertTrue(is_declarative_statement("Я обычно пью таблетки в 22:00"))
        self.assertFalse(is_declarative_statement("Я иду к врачу завтра в 15:00"))
        self.assertFalse(is_declarative_statement("Мне завтра к врачу в 12:00"))

    def test_clear_product_commands_are_detected(self):
        for text in (
            "Покажи календарь на завтра",
            "Что у меня завтра?",
            "Напомни завтра в 9 купить лекарства",
            "Удали напоминание про таблетки",
            "Создай заметку список покупок",
            "Покажи задачи",
            "Найди свободное окно завтра",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_clear_new_command(text))

    def test_confirmation_does_not_swallow_new_command(self):
        pending = {"type": "confirm_delete"}
        self.assertTrue(should_resume_pending(pending, "да"))
        self.assertTrue(should_resume_pending(pending, "нет"))
        self.assertFalse(should_resume_pending(pending, "Покажи календарь на завтра"))

    def test_selection_does_not_swallow_new_command(self):
        pending = {"type": "reminder_select_delete"}
        self.assertTrue(should_resume_pending(pending, "2"))
        self.assertTrue(should_resume_pending(pending, "второе"))
        self.assertFalse(should_resume_pending(pending, "Покажи напоминания"))

    def test_time_prompt_keeps_time_reply_but_allows_interrupt(self):
        pending = {"type": "create_time"}
        self.assertTrue(should_resume_pending(pending, "завтра в 15"))
        self.assertTrue(should_resume_pending(pending, "в 19:30"))
        self.assertFalse(should_resume_pending(pending, "Покажи календарь на завтра"))

    def test_freeform_prompt_keeps_normal_payload(self):
        pending = {"type": "free_slot_title"}
        self.assertTrue(should_resume_pending(pending, "встреча с Иваном"))
        self.assertFalse(should_resume_pending(pending, "Покажи календарь на завтра"))


if __name__ == "__main__":
    unittest.main()
