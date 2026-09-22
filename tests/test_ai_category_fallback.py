from datetime import datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.category_store import create_user_category, update_user_category
from core.db import get_category_colors, get_or_create_google_user
from modules import category_ai
from modules.calendar import _build_event, _resolve_category
from modules.calendar_event_features import build_all_day_event


class AICategoryFallbackTests(unittest.TestCase):
    def user(self, suffix: str) -> int:
        return get_or_create_google_user(
            f"ai-category-{suffix}",
            f"ai-category-{suffix}@example.test",
            "AI Category",
        )

    def test_ai_receives_user_category_labels_and_returns_existing_key(self):
        user_id = self.user("labels")
        update_user_category(user_id, "rest", label="Скучная бытовуха", color_id="1")
        create_user_category(user_id, "Учёба", "9")

        with (
            patch("modules.category_ai.has_ai_access", return_value=True),
            patch("modules.category_ai.ai.is_ai_available", return_value=True),
            patch("modules.category_ai.ai.complete", return_value="rest") as complete,
        ):
            result = category_ai.classify_event_category(user_id, "помыть пол")

        self.assertEqual(result, ("rest", "1"))
        messages = complete.call_args.args[0]
        prompt = messages[-1]["content"]
        self.assertIn("Скучная бытовуха", prompt)
        self.assertIn("Учёба", prompt)

    def test_ai_cannot_invent_category(self):
        user_id = self.user("invalid")
        with (
            patch("modules.category_ai.has_ai_access", return_value=True),
            patch("modules.category_ai.ai.is_ai_available", return_value=True),
            patch("modules.category_ai.ai.complete", return_value="invented_category"),
        ):
            self.assertIsNone(category_ai.classify_event_category(user_id, "что-то новое"))

    def test_ai_is_not_called_without_feature_access(self):
        user_id = self.user("access")
        with (
            patch("modules.category_ai.has_ai_access", return_value=False),
            patch("modules.category_ai.ai.is_ai_available", return_value=True),
            patch("modules.category_ai.ai.complete") as complete,
        ):
            self.assertIsNone(category_ai.classify_event_category(user_id, "непонятное событие"))
        complete.assert_not_called()

    def test_deterministic_category_wins_without_ai_call(self):
        colors = {
            "work": "6",
            "health": "2",
            "rest": "1",
            "travel": "7",
            "family": "4",
            "personal": "3",
            "other": None,
        }
        with patch("modules.calendar.category_ai.classify_event_category") as fallback:
            result = _resolve_category("рейс в Москву", colors, user_id=42)
        self.assertEqual(result, ("travel", "7"))
        fallback.assert_not_called()

    def test_other_category_uses_ai_fallback_and_user_color(self):
        colors = {
            "work": "6",
            "health": "2",
            "rest": "1",
            "travel": "7",
            "family": "4",
            "personal": "3",
            "other": None,
        }
        with patch(
            "modules.calendar.category_ai.classify_event_category",
            return_value=("personal", "3"),
        ) as fallback:
            result = _resolve_category("вечер с Лёшей", colors, user_id=42)

        self.assertEqual(result, ("personal", "3"))
        fallback.assert_called_once_with(42, "вечер с Лёшей")

    def test_event_builder_applies_ai_category_to_description_and_color(self):
        start = datetime(2026, 9, 23, 19, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        colors = {
            "work": "6",
            "health": "2",
            "rest": "1",
            "travel": "7",
            "family": "4",
            "personal": "3",
            "other": None,
        }
        with patch(
            "modules.calendar.category_ai.classify_event_category",
            return_value=("family", "4"),
        ):
            event = _build_event(
                "вечер с Лёшей",
                start,
                category_colors=colors,
                user_id=42,
            )

        self.assertEqual(event["colorId"], "4")
        self.assertIn("category: family", event["description"])

    def test_all_day_event_uses_same_ai_fallback(self):
        now = datetime(2026, 9, 22, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        colors = {
            "work": "6",
            "health": "2",
            "rest": "1",
            "travel": "7",
            "family": "4",
            "personal": "3",
            "other": None,
        }
        with patch(
            "modules.calendar.category_ai.classify_event_category",
            return_value=("personal", "3"),
        ):
            event = build_all_day_event(
                "важный личный ритуал завтра весь день",
                "Europe/Moscow",
                now,
                category_colors=colors,
                user_id=42,
            )

        self.assertIsNotNone(event)
        self.assertEqual(event["colorId"], "3")
        self.assertIn("category: personal", event["description"])


if __name__ == "__main__":
    unittest.main()
