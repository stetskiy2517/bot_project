from pathlib import Path
from unittest import TestCase


class DesignHuntRegressionTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = Path("web/index.html").read_text(encoding="utf-8")
        cls.event = Path("web/event-editor.js").read_text(encoding="utf-8")
        cls.files = Path("web/file-ingest.js").read_text(encoding="utf-8")
        cls.library = Path("web/library.js").read_text(encoding="utf-8")
        cls.swipes = Path("web/task-swipe.js").read_text(encoding="utf-8")
        cls.tasks = Path("web/task-editor.js").read_text(encoding="utf-8")
        cls.mobile = Path("web/mobile-ui.css").read_text(encoding="utf-8")
        cls.mobile_fixes = Path("web/mobile-ui-fixes.js").read_text(encoding="utf-8")
        cls.notes = Path("web/note-tools.js").read_text(encoding="utf-8")
        cls.reminders = Path("web/reminder-editor.js").read_text(encoding="utf-8")

    def test_01_event_close_is_full_touch_target(self):
        self.assertIn(".event-detail-close { flex:0 0 auto; width:44px; height:44px;", self.event)

    def test_02_file_attach_is_full_touch_target(self):
        self.assertIn(".file-attach-button {\n      width: 44px;\n      height: 44px;", self.files)

    def test_03_chat_input_has_full_touch_height(self):
        self.assertIn(".composer input {\n        flex: 1;\n        min-height: 44px;", self.index)

    def test_04_library_tabs_keep_full_touch_height(self):
        self.assertIn(".library-tab {\n      min-width: 0;\n      min-height: 44px;", self.library)

    def test_05_settings_close_is_full_touch_target(self):
        self.assertIn(".close-button {\n        width: 44px;\n        height: 44px;", self.index)

    def test_06_task_notification_bell_is_full_touch_target(self):
        self.assertIn(".planner-task-bell{display:inline-flex;flex:0 0 auto;width:44px;height:44px;", self.swipes)

    def test_07_snackbar_undo_has_full_touch_height(self):
        self.assertIn(".planner-snackbar-action{flex:0 0 auto;min-height:44px;", self.tasks)

    def test_08_bottom_navigation_text_is_not_tiny(self):
        self.assertIn(".mobile-nav-label", self.mobile)
        self.assertNotIn("font-size: 10.5px;", self.mobile)

    def test_09_note_badges_are_legible(self):
        self.assertIn(".note-card-badge{display:inline-flex;align-items:center;min-height:24px;", self.notes)
        self.assertIn("font-size:11px", self.notes)

    def test_10_reminder_category_chip_is_legible(self):
        self.assertIn(".reminder-category-chip{display:inline-flex;align-items:center;min-height:24px;", self.reminders)
        self.assertIn("font-size:11px", self.reminders)

    def test_11_narrow_library_tabs_are_not_squeezed(self):
        self.assertIn("@media (max-width:360px)", self.library)
        self.assertIn(".library-tab { min-width:44px; }", self.library)

    def test_12_mobile_message_input_has_actual_touch_height(self):
        self.assertIn(".app.mobile-shell #message", self.mobile)
        self.assertIn("height: 44px;", self.mobile)
        self.assertIn("min-height: 44px;", self.mobile_fixes)
        self.assertIn("Math.max(44, editor.scrollHeight || 44)", self.mobile_fixes)


if __name__ == "__main__":
    import unittest
    unittest.main()
