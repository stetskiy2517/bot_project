from __future__ import annotations

from datetime import datetime
import unittest
import uuid
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core import db
from modules import daily_review


class DailyReviewPresentationTests(unittest.TestCase):
    def setUp(self):
        token = uuid.uuid4().hex
        self.user_id = db.get_or_create_google_user(
            f"daily-review-{token}",
            f"daily-review-{token}@example.test",
            "Daily Review Test",
        )
        db.save_user_timezone(self.user_id, "Europe/Moscow")
        with db.db_lock:
            db.conn.execute("DELETE FROM daily_review_ai_cache WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    def tearDown(self):
        with db.db_lock:
            db.conn.execute("DELETE FROM daily_review_ai_cache WHERE user_id=?", (self.user_id,))
            db.conn.commit()

    def test_free_review_never_calls_ai_provider(self):
        source = "Утренняя сводка\nДоброе утро. Сегодня встреч нет."
        with (
            patch.object(daily_review, "has_ai_access", return_value=False),
            patch.object(daily_review, "is_ai_available", return_value=True),
            patch.object(daily_review, "complete") as complete,
        ):
            text, presentation = daily_review._enhance_review_text(
                self.user_id, "morning", "2026-09-17", source
            )
        self.assertEqual(text, source)
        self.assertEqual(presentation, "free")
        complete.assert_not_called()

    def test_paid_review_uses_ai_once_then_cache(self):
        source = (
            "Утренняя сводка\n"
            "Доброе утро. Коротко о главном на сегодня.\n"
            "Сегодня встреч в календаре нет.\n"
            "По задачам всё спокойно: открыто 2, просроченных нет."
        )
        ai_text = (
            "Утренняя сводка\n"
            "Доброе утро. День выглядит спокойно: встреч нет, а по задачам просрочки нет. "
            "Сейчас открыты две задачи."
        )
        with (
            patch.object(daily_review, "has_ai_access", return_value=True),
            patch.object(daily_review, "is_ai_available", return_value=True),
            patch.object(daily_review, "complete", return_value=ai_text) as complete,
        ):
            first = daily_review._enhance_review_text(
                self.user_id, "morning", "2026-09-17", source
            )
            second = daily_review._enhance_review_text(
                self.user_id, "morning", "2026-09-17", source
            )
        self.assertEqual(first, (ai_text, "ai"))
        self.assertEqual(second, (ai_text, "ai"))
        complete.assert_called_once()

    def test_ai_failure_falls_back_to_free_review(self):
        source = "Вечерний разбор\nДобрый вечер. На сегодня всё спокойно."
        with (
            patch.object(daily_review, "has_ai_access", return_value=True),
            patch.object(daily_review, "is_ai_available", return_value=True),
            patch.object(daily_review, "complete", side_effect=daily_review.AIError("offline")),
        ):
            text, presentation = daily_review._enhance_review_text(
                self.user_id, "evening", "2026-09-17", source
            )
        self.assertEqual(text, source)
        self.assertEqual(presentation, "free")

    def test_contiguous_half_hour_slots_are_merged(self):
        zone = ZoneInfo("Europe/Moscow")
        slots = [
            (
                datetime(2026, 9, 17, 14, 30, tzinfo=zone),
                datetime(2026, 9, 17, 15, 0, tzinfo=zone),
            ),
            (
                datetime(2026, 9, 17, 15, 0, tzinfo=zone),
                datetime(2026, 9, 17, 15, 30, tzinfo=zone),
            ),
            (
                datetime(2026, 9, 17, 15, 30, tzinfo=zone),
                datetime(2026, 9, 17, 16, 0, tzinfo=zone),
            ),
        ]
        self.assertEqual(
            daily_review._format_free_slots(slots),
            "Ближайшее свободное время: 14:30–16:00.",
        )

    def test_attention_body_does_not_repeat_review_heading(self):
        self.assertEqual(
            daily_review._strip_review_heading(
                "Утренняя сводка\nДоброе утро. Сегодня день спокойный."
            ),
            "Доброе утро. Сегодня день спокойный.",
        )


if __name__ == "__main__":
    unittest.main()
