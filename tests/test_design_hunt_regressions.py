from pathlib import Path
from unittest import TestCase


class DesignHuntRegressionTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.event = Path("web/event-editor.js").read_text(encoding="utf-8")
        cls.attention = Path("web/attention-center.css").read_text(encoding="utf-8")
        cls.life = Path("web/life-balance.js").read_text(encoding="utf-8")
        cls.tasks = Path("web/task-editor.js").read_text(encoding="utf-8")
        cls.notes = Path("web/note-tools.js").read_text(encoding="utf-8")
        cls.reminders = Path("web/reminder-editor.js").read_text(encoding="utf-8")

    def test_01_event_close_is_full_touch_target(self):
        self.assertIn(".event-detail-close { flex:0 0 auto; width:44px; height:44px;", self.event)

    def test_02_attention_actions_are_full_touch_targets(self):
        self.assertIn("min-height: 44px;", self.attention)
        self.assertNotIn("min-height: 31px;", self.attention)

    def test_03_attention_ai_badge_is_not_nine_pixels(self):
        self.assertNotIn("font-size: 9px;", self.attention)

    def test_04_attention_action_text_is_readable(self):
        self.assertNotIn("font-size: 10px;", self.attention)

    def test_05_attention_count_is_readable(self):
        self.assertIn(".mobile-attention-count", self.attention)
        self.assertIn("font-size: 11px;", self.attention)

    def test_06_life_balance_labels_are_not_ten_pixels(self):
        self.assertIn(".life-balance-row label", self.life)
        self.assertNotIn("font-size:10px}", self.life)

    def test_07_life_balance_inputs_have_touch_height(self):
        self.assertIn(".life-balance-row input{width:100%;min-height:44px;", self.life)

    def test_08_snackbar_undo_has_touch_height(self):
        self.assertIn(".planner-snackbar-action{flex:0 0 auto;min-height:44px;", self.tasks)

    def test_09_note_badges_are_legible(self):
        self.assertIn("min-height:24px", self.notes)
        self.assertIn("font-size:11px", self.notes)

    def test_10_reminder_category_chip_is_legible(self):
        self.assertIn(".reminder-category-chip{display:inline-flex;align-items:center;min-height:24px;", self.reminders)
        self.assertIn("font-size:11px", self.reminders)


if __name__ == "__main__":
    import unittest
    unittest.main()
