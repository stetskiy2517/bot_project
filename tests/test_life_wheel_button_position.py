from pathlib import Path
import unittest


class LifeWheelButtonPositionTests(unittest.TestCase):
    def test_button_is_pinned_next_to_settings_on_the_right(self):
        script = Path("web/life-wheel.js").read_text(encoding="utf-8")
        self.assertIn("position:absolute", script)
        self.assertIn("right:70px", script)
        self.assertIn("top:calc(env(safe-area-inset-top) + 14px)", script)
        self.assertNotIn(".topbar { gap: 8px; }", script)


if __name__ == "__main__":
    unittest.main()
