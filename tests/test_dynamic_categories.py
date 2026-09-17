from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

import web_app
from core import db
from core.category_store import (
    create_user_category,
    delete_user_category,
    get_category_colors,
    get_user_categories,
    update_user_category,
)
from modules.category_runtime import install_category_detector_guard
from modules.event_detail_api import _event_payload
from modules.language_support import install_english_category_support
from modules.life_wheel import build_life_wheel_snapshot
from tests.web_test_support import web_test_app


class DynamicCategoryStoreTests(unittest.TestCase):
    def user(self, suffix: str) -> int:
        return db.get_or_create_google_user(
            f"dynamic-category-{suffix}",
            f"dynamic-category-{suffix}@example.test",
            "Category User",
        )

    def test_defaults_can_be_renamed_without_losing_semantic_key(self):
        user_id = self.user("rename")
        updated = update_user_category(user_id, "work", label="Business")
        self.assertEqual(updated["key"], "work")
        self.assertEqual(updated["semantic_key"], "work")
        self.assertEqual(updated["label"], "Business")

    def test_custom_category_can_be_created_colored_and_deleted(self):
        user_id = self.user("custom")
        created = create_user_category(user_id, "Учёба", "9")
        self.assertTrue(created["key"].startswith("custom_"))
        self.assertEqual(created["color_id"], "9")
        self.assertIn(created["key"], get_category_colors(user_id))
        renamed = update_user_category(user_id, created["key"], label="Learning", color_id="2")
        self.assertEqual(renamed["label"], "Learning")
        self.assertEqual(renamed["color_id"], "2")
        self.assertTrue(delete_user_category(user_id, created["key"]))
        self.assertNotIn(created["key"], {item["key"] for item in get_user_categories(user_id)})

    def test_deleted_default_is_not_resurrected_by_legacy_color_save(self):
        user_id = self.user("deleted-default")
        self.assertTrue(delete_user_category(user_id, "travel"))
        db.save_calendar_preferences(user_id, category_colors={"travel": "11"})
        self.assertNotIn("travel", {item["key"] for item in get_user_categories(user_id)})

    def test_last_category_cannot_be_deleted(self):
        user_id = self.user("keep-one")
        keys = [item["key"] for item in get_user_categories(user_id)]
        for key in keys[:-1]:
            self.assertTrue(delete_user_category(user_id, key))
        with self.assertRaises(ValueError):
            delete_user_category(user_id, keys[-1])


class EnglishLifeWheelTests(unittest.TestCase):
    def setUp(self):
        install_english_category_support()
        install_category_detector_guard()
        self.zone = ZoneInfo("Europe/Moscow")
        self.now = datetime(2026, 9, 14, 20, 0, tzinfo=self.zone)
        suffix = self._testMethodName
        self.user_id = db.get_or_create_google_user(
            f"wheel-en-{suffix}",
            f"wheel-en-{suffix}@example.test",
            "English Wheel",
        )

    def timed_event(self, title: str, category: str | None = None) -> dict:
        description = f"AI Smart Planner category: {category}" if category else ""
        return {
            "summary": title,
            "description": description,
            "status": "confirmed",
            "transparency": "opaque",
            "start": {"dateTime": "2026-09-12T10:00:00+03:00", "timeZone": "Europe/Moscow"},
            "end": {"dateTime": "2026-09-12T11:00:00+03:00", "timeZone": "Europe/Moscow"},
        }

    def test_english_legacy_event_is_counted_as_work(self):
        result = build_life_wheel_snapshot(
            self.user_id,
            now=self.now,
            events=[self.timed_event("Client meeting in the office")],
            reminders=[],
            tasks=[],
        )
        by_key = {item["key"]: item for item in result["categories"]}
        self.assertEqual(by_key["work"]["events"], 1)
        self.assertGreater(by_key["work"]["score"], 0)

    def test_english_health_and_family_categories_are_detected(self):
        events = [
            self.timed_event("Dentist appointment"),
            {
                **self.timed_event("Dinner with family"),
                "start": {"dateTime": "2026-09-13T18:00:00+03:00", "timeZone": "Europe/Moscow"},
                "end": {"dateTime": "2026-09-13T19:00:00+03:00", "timeZone": "Europe/Moscow"},
            },
        ]
        result = build_life_wheel_snapshot(
            self.user_id,
            now=self.now,
            events=events,
            reminders=[],
            tasks=[],
        )
        by_key = {item["key"]: item for item in result["categories"]}
        self.assertEqual(by_key["health"]["events"], 1)
        self.assertEqual(by_key["family"]["events"], 1)

    def test_custom_category_appears_in_wheel_and_event_editor_payload(self):
        custom = create_user_category(self.user_id, "Learning", "9")
        event = self.timed_event("Read architecture book", custom["key"])
        result = build_life_wheel_snapshot(
            self.user_id,
            now=self.now,
            events=[event],
            reminders=[],
            tasks=[],
        )
        by_key = {item["key"]: item for item in result["categories"]}
        self.assertIn(custom["key"], by_key)
        self.assertEqual(by_key[custom["key"]]["label"], "Learning")
        self.assertEqual(by_key[custom["key"]]["events"], 1)

        payload = _event_payload(event, "Europe/Moscow", self.user_id)
        self.assertEqual(payload["category"], custom["key"])
        self.assertEqual(payload["category_label"], "Learning")
        self.assertIn(custom["key"], {item["key"] for item in payload["category_options"]})


class DynamicCategoryApiTests(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        suffix = self._testMethodName
        self.user_id = db.get_or_create_google_user(
            f"category-api-{suffix}",
            f"category-api-{suffix}@example.test",
            "Category API",
        )
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user_id

    def test_category_manager_is_loaded_on_root(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<script src="/category-manager.js"></script>', response.get_data(as_text=True))
        script = self.client.get("/category-manager.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("/api/categories", script.get_data(as_text=True))

    def test_category_crud_api(self):
        initial = self.client.get("/api/categories")
        self.assertEqual(initial.status_code, 200)
        self.assertGreaterEqual(len(initial.get_json()["categories"]), 1)

        created = self.client.post("/api/categories", json={"label": "Учёба", "color_id": "9"})
        self.assertEqual(created.status_code, 201)
        category = created.get_json()["category"]

        updated = self.client.patch(
            f"/api/categories/{category['key']}",
            json={"label": "Learning", "color_id": "2"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["category"]["label"], "Learning")

        deleted = self.client.delete(f"/api/categories/{category['key']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(deleted.get_json()["events_deleted"])


if __name__ == "__main__":
    unittest.main()
