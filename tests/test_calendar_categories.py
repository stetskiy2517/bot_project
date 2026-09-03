import unittest

from core.db import DEFAULT_CATEGORY_COLORS, get_category_colors, reset_category_colors, save_category_colors
from modules.calendar_categories import apply_user_category, detect_category


class CalendarCategoryTests(unittest.TestCase):
    def test_default_categories(self):
        self.assertEqual(detect_category("встреча с клиентом завтра"), "work")
        self.assertEqual(detect_category("невролог завтра"), "health")
        self.assertEqual(detect_category("тренировка вечером"), "rest")
        self.assertEqual(detect_category("Полет в Саратов 18 сентября"), "travel")
        self.assertEqual(detect_category("забрать ребенка"), "personal")
        self.assertEqual(detect_category("прочитать книгу"), "other")

    def test_user_color_overrides_default(self):
        user_id = 910001
        reset_category_colors(user_id)
        self.assertEqual(get_category_colors(user_id)["travel"], DEFAULT_CATEGORY_COLORS["travel"])
        save_category_colors(user_id, {"travel": "11", "work": "9"})
        colors = get_category_colors(user_id)
        self.assertEqual(colors["travel"], "11")
        self.assertEqual(colors["work"], "9")
        self.assertEqual(colors["health"], DEFAULT_CATEGORY_COLORS["health"])

    def test_category_colors_are_isolated_per_user(self):
        a, b = 910002, 910003
        reset_category_colors(a); reset_category_colors(b)
        save_category_colors(a, {"health": "11"})
        save_category_colors(b, {"health": "2"})
        self.assertEqual(get_category_colors(a)["health"], "11")
        self.assertEqual(get_category_colors(b)["health"], "2")

    def test_apply_user_category_sets_google_color_and_metadata(self):
        user_id = 910004
        reset_category_colors(user_id)
        save_category_colors(user_id, {"travel": "9"})
        event = {"summary": "Полет в Саратов", "description": "AI Smart Planner category: other"}
        apply_user_category(event, "Полет в Саратов 18 сентября в 18:00", user_id)
        self.assertEqual(event["colorId"], "9")
        self.assertIn("category: travel", event["description"])

    def test_invalid_color_is_rejected(self):
        with self.assertRaises(ValueError):
            save_category_colors(910005, {"work": "99"})


if __name__ == "__main__":
    unittest.main()
