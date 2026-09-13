from pathlib import Path
import unittest


class ReminderActionTouchTests(unittest.TestCase):
    def test_swipe_actions_own_their_touch_area(self):
        script = Path("web/library.js").read_text(encoding="utf-8")

        self.assertIn("pointer-events: none;", script)
        self.assertIn(".reminder-actions.delete-side", script)
        self.assertIn("right: auto;", script)
        self.assertIn("width: 88px;", script)
        self.assertIn(".reminder-actions.manage-side", script)
        self.assertIn("left: auto;", script)
        self.assertIn("width: max-content;", script)
        self.assertIn("pointer-events: auto;", script)
        self.assertIn("touch-action: manipulation;", script)
        self.assertIn("function isolateLibraryActionPointer(button)", script)
        self.assertIn('button.addEventListener("pointerdown", stopPointer)', script)
        self.assertIn('button.addEventListener("pointerup", stopPointer)', script)
        self.assertIn("isolateLibraryActionPointer(deleteButton)", script)
        self.assertIn("isolateLibraryActionPointer(completeButton)", script)
        self.assertIn("isolateLibraryActionPointer(rescheduleButton)", script)
        self.assertIn("event.stopPropagation();\n      handleLibraryAction", script)


if __name__ == "__main__":
    unittest.main()
