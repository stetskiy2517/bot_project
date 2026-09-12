import asyncio
import unittest

from core.web_transport import WebMessage, _web_text


class WebTransportReplyTests(unittest.TestCase):
    def test_compacts_timed_calendar_creation_reply(self):
        reply = _web_text(
            "Событие «Встреча» добавлено: 13.09.2026 14:00–15:00 (Europe/Moscow)",
            "Встреча завтра в 14:00.",
        )
        self.assertEqual(reply, "Готово · «Встреча»\nЗавтра, 14:00–15:00")
        self.assertNotIn("Europe/Moscow", reply)
        self.assertNotIn("2026", reply)

    def test_compacts_calendar_creation_reply_extras(self):
        reply = _web_text(
            "Событие «Созвон» добавлено: 18.09.2026 10:00–11:00 (Europe/Moscow) · повторяется, с напоминанием, место: офис",
            "созвон 18 сентября в 10 повторять каждую неделю с напоминанием место офис",
        )
        self.assertEqual(
            reply,
            "Готово · «Созвон»\n18.09, 10:00–11:00\nПовторяется · напоминание · место: офис",
        )

    def test_compacts_all_day_calendar_creation_reply(self):
        reply = _web_text(
            "Событие «Командировка» добавлено на весь день: 2026-09-14",
            "командировка послезавтра на весь день",
        )
        self.assertEqual(reply, "Готово · «Командировка»\nПослезавтра, весь день")

    def test_other_replies_are_unchanged(self):
        self.assertEqual(_web_text("На завтра событий нет.", "что у меня завтра"), "На завтра событий нет.")

    def test_web_message_uses_original_request_for_relative_day_label(self):
        message = WebMessage(text="встреча завтра в 14")
        asyncio.run(
            message.reply_text(
                "Событие «Встреча» добавлено: 13.09.2026 14:00–15:00 (Europe/Moscow)"
            )
        )
        self.assertEqual(message.replies, ["Готово · «Встреча»\nЗавтра, 14:00–15:00"])


if __name__ == "__main__":
    unittest.main()
