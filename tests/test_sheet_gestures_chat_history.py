import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SWIPE_SCRIPT = ROOT / "web" / "swipe-navigation.js"
NOTE_SCRIPT = ROOT / "web" / "note-tools.js"


class SheetGesturesAndChatHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = SWIPE_SCRIPT.read_text(encoding="utf-8")
        cls.note_script = NOTE_SCRIPT.read_text(encoding="utf-8")

    def test_handle_sheets_support_downward_dismiss(self):
        self.assertIn("const SWIPE_DOWN_MIN_Y = 72", self.script)
        self.assertIn("const SHEET_TOP_GRAB_ZONE = 88", self.script)
        self.assertIn("const SHEET_HANDLE_SELECTOR = \".handle, [class*='-handle']\"", self.script)
        self.assertIn('document.addEventListener("touchmove"', self.script)
        self.assertIn("dy >= SWIPE_DOWN_MIN_Y", self.script)
        self.assertIn("updateSheetDrag(sheetGesture, touch.clientY)", self.script)
        self.assertIn("animateSheetDismiss(start)", self.script)
        self.assertIn("animateSheetBack(start)", self.script)

    def test_downward_dismiss_animates_with_native_feeling_motion(self):
        self.assertIn("const SHEET_SNAP_MS = 180", self.script)
        self.assertIn("const SHEET_DISMISS_MS = 220", self.script)
        self.assertIn("translate3d(0, ${offset.toFixed(1)}px, 0)", self.script)
        self.assertIn("backdropColorWithProgress", self.script)
        self.assertIn("SHEET_FAST_DISMISS_VELOCITY", self.script)

    def test_sheet_close_uses_module_close_controls(self):
        for selector in (
            "[data-note-close]",
            "[data-note-editor-cancel]",
            "[data-reminder-edit-cancel]",
            "[data-task-editor-cancel]",
            "#closeLifeWheel",
            "#closeSettings",
            ".event-detail-close",
        ):
            self.assertIn(selector, self.script)
        self.assertIn("return closeSheetRoot(activeBackSheet())", self.script)
        self.assertIn('return !root.classList.contains("open")', self.script)

    def test_note_sheet_has_local_right_swipe_fallback(self):
        self.assertIn('root.addEventListener("touchstart"', self.note_script)
        self.assertIn('root.addEventListener("touchend"', self.note_script)
        self.assertIn("dx < 64", self.note_script)
        self.assertIn('root.addEventListener("wheel"', self.note_script)

    def test_chat_retains_only_last_fourteen_messages(self):
        self.assertIn("const CHAT_HISTORY_MAX_MESSAGES = 14", self.script)
        self.assertIn('const CHAT_HISTORY_KEY_PREFIX = "personal-secretary-chat-history-v1"', self.script)
        self.assertIn("localStorage.setItem(chatHistoryKey, JSON.stringify(history))", self.script)
        self.assertIn("restoreChatHistory()", self.script)
        self.assertIn("nodes.slice(0, -CHAT_HISTORY_MAX_MESSAGES).forEach(node => node.remove())", self.script)

    def test_empty_dom_cleanup_does_not_erase_retained_history(self):
        self.assertIn("if (!history.length) return;", self.script)
        self.assertIn("volatileChatHistory", self.script)
        self.assertIn('app.classList.contains("chat-active")', self.script)


if __name__ == "__main__":
    unittest.main()
