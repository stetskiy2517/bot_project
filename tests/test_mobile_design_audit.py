from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MobileDesignAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mobile = (ROOT / "web" / "mobile-ui.css").read_text(encoding="utf-8")
        cls.overlays = (ROOT / "web" / "mobile-ui-overlays.css").read_text(encoding="utf-8")
        cls.mobile_js = (ROOT / "web" / "mobile-ui.js").read_text(encoding="utf-8")
        cls.tasks = (ROOT / "web" / "tasks.js").read_text(encoding="utf-8")
        cls.notes = (ROOT / "web" / "note-tools.js").read_text(encoding="utf-8")
        cls.task_editor = (ROOT / "web" / "task-editor.js").read_text(encoding="utf-8")
        cls.reminder_editor = (ROOT / "web" / "reminder-editor.js").read_text(encoding="utf-8")

    def test_no_invisible_compatibility_hit_target(self):
        self.assertNotIn("2147483647", self.overlays)
        self.assertNotIn(".app.mobile-shell #libraryOpenBtn", self.overlays)
        self.assertIn("window.PlannerLibrary", self.mobile_js)
        self.assertNotIn('getElementById("libraryOpenBtn")', self.mobile_js)

    def test_top_right_controls_have_44px_touch_targets(self):
        self.assertIn("right: 66px;\n  width: 44px;\n  height: 44px;", self.mobile)
        self.assertIn("right: 14px;\n  width: 44px;\n  height: 44px;", self.mobile)

    def test_generic_mobile_icon_buttons_are_touch_safe(self):
        self.assertIn(".mobile-icon-button {\n  width: 44px;\n  height: 44px;", self.mobile)

    def test_today_card_icons_are_not_32px_anymore(self):
        self.assertIn(".mobile-card-icon-button {\n  width: 44px;\n  height: 44px;", self.mobile)
        self.assertNotIn(".mobile-card-icon-button {\n  width: 32px;", self.mobile)

    def test_mobile_action_buttons_have_minimum_touch_height(self):
        self.assertIn(".mobile-action-button {\n  min-height: 44px;", self.mobile)

    def test_task_header_and_task_actions_have_touch_safe_sizes(self):
        self.assertIn(".planner-task-header-icon{position:relative;width:44px;height:44px;flex:0 0 44px;", self.tasks)
        self.assertIn(".planner-task-action{min-height:44px;", self.tasks)
        self.assertNotIn("width:34px;height:34px;flex-basis:34px", self.tasks)

    def test_note_header_and_close_controls_have_touch_safe_sizes(self):
        self.assertIn(".notes-header-icon{position:relative;width:44px;height:44px;flex:0 0 44px;", self.notes)
        self.assertIn(".note-window-close{flex:0 0 auto;width:44px;height:44px;", self.notes)
        self.assertNotIn("width:34px;height:34px;flex-basis:34px", self.notes)

    def test_editor_back_controls_are_consistent(self):
        self.assertIn(".task-editor-close{display:inline-flex;width:44px;height:44px;", self.task_editor)
        self.assertIn(".reminder-edit-back{display:inline-flex;width:44px;height:44px;", self.reminder_editor)

    def test_small_mobile_text_is_readable(self):
        self.assertIn("font-size: 10.5px;", self.mobile)
        self.assertIn(".mobile-summary-label {\n  color: #7d7d78;\n  font-size: 11px;", self.mobile)
        self.assertIn("font-size: 14px;", self.mobile)
        self.assertIn("font-size: 12px;", self.mobile)

    def test_fields_and_keyboard_focus_are_visible(self):
        self.assertIn("min-height: 48px;", self.mobile)
        self.assertIn("button:focus-visible", self.mobile)
        self.assertIn('outline: 2px solid #4f4f4b;', self.mobile)


if __name__ == "__main__":
    unittest.main()
