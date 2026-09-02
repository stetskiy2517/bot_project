import unittest

from modules.router import (
    INTENT_CREATE,
    INTENT_DELETE,
    INTENT_FREE,
    INTENT_UNKNOWN,
    INTENT_UPDATE,
    INTENT_VIEW,
    _needs_time,
    detect_intent,
)


class RouterIntentTests(unittest.TestCase):
    def test_explicit_create(self):
        self.assertEqual(detect_intent("поставь врача завтра в 19").name, INTENT_CREATE)
        self.assertEqual(detect_intent("добавь встречу в пятницу в 10:30").name, INTENT_CREATE)

    def test_natural_create_without_command_verb(self):
        self.assertEqual(detect_intent("невролог в пятницу в 19:00").name, INTENT_CREATE)
        self.assertEqual(detect_intent("созвон завтра в 9").name, INTENT_CREATE)

    def test_view(self):
        self.assertEqual(detect_intent("что у меня завтра?").name, INTENT_VIEW)
        self.assertEqual(detect_intent("покажи календарь на пятницу").name, INTENT_VIEW)

    def test_update(self):
        self.assertEqual(detect_intent("перенеси врача на субботу").name, INTENT_UPDATE)

    def test_delete(self):
        self.assertEqual(detect_intent("отмени стоматолога завтра").name, INTENT_DELETE)

    def test_free_slots(self):
        self.assertEqual(detect_intent("когда свободен завтра после обеда?").name, INTENT_FREE)

    def test_plain_statement_is_not_event(self):
        self.assertEqual(detect_intent("сейчас 19:00, я уже дома").name, INTENT_UNKNOWN)
        self.assertEqual(detect_intent("завтра будет сложный день").name, INTENT_UNKNOWN)

    def test_missing_time_requires_clarification(self):
        self.assertTrue(_needs_time("поставь врача завтра"))
        self.assertTrue(_needs_time("создай встречу в пятницу"))
        self.assertFalse(_needs_time("поставь врача завтра в 19:00"))
        self.assertFalse(_needs_time("поставь врача в 19:00"))


if __name__ == "__main__":
    unittest.main()
