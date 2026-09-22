from datetime import datetime
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

import web_app
from tests.web_test_support import web_test_app
from core.db import DEFAULT_CATEGORY_COLORS, get_or_create_google_user
from modules.calendar import EVENT_CATEGORIES, _detect_category
from modules.calendar_event_features import build_all_day_event


class CategoryBetaMatrixTests(unittest.TestCase):
    def test_backend_category_sets_do_not_drift(self):
        self.assertEqual(set(EVENT_CATEGORIES), set(DEFAULT_CATEGORY_COLORS) - {"other"})

    def test_family_language_matrix(self):
        cases = [
            "ужин с семьей",
            "встреча с семьёй завтра в 19",
            "забрать ребенка из школы",
            "поездка с детьми",
            "ужин с родителями",
            "день рождения дочери",
            "позвонить маме",
            "семейный ужин",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(_detect_category(text)[0], "family")

    def test_family_priority_over_generic_work_and_travel_words(self):
        self.assertEqual(_detect_category("встреча с родителями")[0], "family")
        self.assertEqual(_detect_category("поездка с детьми")[0], "family")

    def test_number_seven_does_not_trigger_family(self):
        self.assertNotEqual(_detect_category("семь звонков клиентам")[0], "family")

    def test_other_categories_still_work(self):
        cases = {
            "рабочая встреча с клиентом": "work",
            "прием у невролога": "health",
            "кино с друзьями": "rest",
            "рейс в Москву": "travel",
            "купить продукты": "personal",
            "непонятное событие": "other",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_detect_category(text)[0], expected)

    def test_everyday_personal_phrases_do_not_fall_into_other(self):
        cases = [
            "ужин с Лешкой",
            "уборка квартиры",
            "постирать вещи",
            "приготовить ужин",
            "я тебя люблю",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(_detect_category(text)[0], "personal")

    def test_common_work_phrase_is_detected(self):
        self.assertEqual(_detect_category("сделать текст рассылки")[0], "work")

    def test_family_uses_user_color_override(self):
        colors = dict(DEFAULT_CATEGORY_COLORS)
        colors["family"] = "11"
        self.assertEqual(_detect_category("ужин с родителями", colors), ("family", "11"))

    def test_family_color_applies_to_all_day_event(self):
        now = datetime(2026, 9, 12, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        event = build_all_day_event(
            "день рождения дочери завтра",
            "Europe/Moscow",
            now,
            category_colors=DEFAULT_CATEGORY_COLORS,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["colorId"], "4")
        self.assertIn("category: family", event["description"])

    def test_web_settings_expose_family(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        self.assertIn('family: "Семья"', html)
        self.assertIn('family: "4"', html)


class CategorySettingsCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        suffix = self._testMethodName
        uid = get_or_create_google_user(
            f"beta-family-compat-{suffix}",
            f"beta-family-compat-{suffix}@example.test",
            "Beta Family",
        )
        with self.client.session_transaction() as session:
            session["user_id"] = uid

    def test_legacy_color_payload_without_family_remains_valid(self):
        legacy = {
            "work": "3",
            "health": "6",
            "rest": "10",
            "travel": "7",
            "personal": "5",
            "other": None,
        }
        response = self.client.post("/api/settings", json={"category_colors": legacy})
        self.assertEqual(response.status_code, 200)
        saved = response.get_json()["preferences"]["category_colors"]
        self.assertEqual(saved["family"], "4")
        for category, color in legacy.items():
            self.assertEqual(saved[category], color)

    def test_family_color_can_be_changed(self):
        response = self.client.post("/api/settings", json={"category_colors": {"family": "11"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["preferences"]["category_colors"]["family"], "11")

    def test_unknown_category_is_rejected(self):
        response = self.client.post("/api/settings", json={"category_colors": {"alien": "1"}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_settings")


if __name__ == "__main__":
    unittest.main()
