import unittest

from core.conversation_policy import (
    is_clear_new_command,
    is_declarative_statement,
    pending_retarget_fresh_payload,
    pending_retarget_target,
    should_resume_pending,
)


class ConversationPolicyTests(unittest.TestCase):
    def test_routine_and_medication_facts_are_declarative(self):
        self.assertTrue(is_declarative_statement("Я принимаю таблетки завтра в 23:00"))
        self.assertTrue(is_declarative_statement("Я обычно пью таблетки в 22:00"))
        self.assertTrue(is_declarative_statement("Я завтра в 23:00 принимаю таблетки"))
        self.assertTrue(is_declarative_statement("Я каждый вечер в 22 пью таблетки"))
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
            "Когда завтра свободно?",
            "Когда в пятницу есть свободное время?",
            "Есть ли свободное окно завтра?",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_clear_new_command(text))

    def test_confirmation_does_not_swallow_new_command(self):
        pending = {"type": "confirm_delete"}
        self.assertTrue(should_resume_pending(pending, "да"))
        self.assertTrue(should_resume_pending(pending, "нет"))
        self.assertFalse(should_resume_pending(pending, "Покажи календарь на завтра"))
        self.assertFalse(should_resume_pending(pending, "Когда завтра свободно?"))

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
        self.assertFalse(should_resume_pending(pending, "Когда завтра свободно?"))

    def test_pending_retarget_extracts_only_entity_corrections(self):
        cases = {
            "Не событие, а задача": "task",
            "Причем тут событие? В задачу добавь": "task",
            "Сделай это напоминанием": "reminder",
            "Не задача, а заметка": "note",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(pending_retarget_target(text), expected)

        self.assertIsNone(pending_retarget_target("Создай задачу купить молоко"))
        self.assertIsNone(pending_retarget_target("Добавь встречу завтра в 15:00"))
        self.assertIsNone(pending_retarget_target("Это задача: купить молоко"))
        self.assertIsNone(pending_retarget_target("Не событие, а задача купить молоко"))

    def test_fresh_entity_correction_keeps_new_payload_not_stale_payload(self):
        cases = {
            "Это задача: купить молоко": ("task", "купить молоко"),
            "Не событие, а задача купить молоко": ("task", "купить молоко"),
            "Пусть будет заметка позвонить врачу": ("note", "позвонить врачу"),
            "В задачу добавь забрать паспорт": ("task", "забрать паспорт"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(pending_retarget_fresh_payload(text), expected)

        self.assertIsNone(pending_retarget_fresh_payload("Не событие, а задача"))

    def test_entity_correction_interrupts_stale_pending(self):
        for text in (
            "Не событие, а задача",
            "Это задача: купить молоко",
            "Пусть будет напоминание",
        ):
            with self.subTest(text=text):
                self.assertFalse(
                    should_resume_pending({"type": "create_time"}, text)
                )

    def test_freeform_prompt_keeps_normal_payload(self):
        pending = {"type": "free_slot_title"}
        self.assertTrue(should_resume_pending(pending, "встреча с Иваном"))
        self.assertFalse(should_resume_pending(pending, "Покажи календарь на завтра"))


if __name__ == "__main__":
    unittest.main()
