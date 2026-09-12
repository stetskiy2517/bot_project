import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from modules.router import _normalise_pending_reply, _resume_pending


class PendingReplyNormalizationTests(unittest.IsolatedAsyncioTestCase):
    def test_confirmation_punctuation_is_removed(self):
        self.assertEqual(_normalise_pending_reply("Да."), "Да")
        self.assertEqual(_normalise_pending_reply("нет!"), "нет")
        self.assertEqual(_normalise_pending_reply("«Окей.»"), "Окей")
        self.assertEqual(_normalise_pending_reply("2."), "2")

    def test_regular_pending_text_keeps_punctuation(self):
        self.assertEqual(
            _normalise_pending_reply("Встреча с Ивановым."),
            "Встреча с Ивановым.",
        )

    async def test_punctuated_yes_is_forwarded_as_confirmation(self):
        pending = {"type": "delete_bulk_confirm", "events": [{"id": "1"}]}
        context = SimpleNamespace(user_data={"smart_planner_pending": pending})
        update = SimpleNamespace(message=SimpleNamespace())

        with patch(
            "modules.router.resume_pending_action",
            new_callable=AsyncMock,
            return_value=True,
        ) as resume:
            handled = await _resume_pending(update, context, "Да.")

        self.assertTrue(handled)
        resume.assert_awaited_once()
        self.assertEqual(resume.await_args.args[2], "Да")
        self.assertIs(resume.await_args.args[3], pending)


if __name__ == "__main__":
    unittest.main()
