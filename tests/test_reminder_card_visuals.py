import unittest

import web_app


class ReminderCardVisualTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()

    def test_reminder_cards_hide_status_badges_and_strike_completed_items(self):
        response = self.client.get("/library.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()

        self.assertNotIn("library-card-status", script)
        self.assertNotIn("library-card.delivered { opacity", script)
        self.assertNotIn("library-card.completed { opacity", script)
        self.assertIn(".library-card.completed .library-card-title", script)
        self.assertIn("text-decoration: line-through", script)
        self.assertIn("background: #fff;", script)
        self.assertIn('return "";', script)


if __name__ == "__main__":
    unittest.main()
