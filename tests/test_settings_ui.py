from pathlib import Path
import unittest


class SettingsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")

    def test_category_colors_are_collapsed_into_group(self):
        self.assertIn('<details id="categoryColorsGroup" class="settings-group">', self.html)
        self.assertIn('<span class="settings-group-title">Цвета категорий</span>', self.html)
        self.assertIn('<span class="settings-group-meta">7 категорий</span>', self.html)
        self.assertIn('id="categoryColors"', self.html)
        self.assertNotIn('<details id="categoryColorsGroup" class="settings-group" open', self.html)


if __name__ == "__main__":
    unittest.main()
