import unittest

import web_app


class VoiceGestureAssetTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()

    def test_voice_gesture_script_is_loaded(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        response.close()
        self.assertIn('<script src="/voice-gesture.js"></script>', html)

    def test_voice_gesture_asset_contains_cancel_flow(self):
        response = self.client.get("/voice-gesture.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()
        self.assertIn("const CANCEL_SWIPE_PX = 72", script)
        self.assertIn('button.addEventListener("pointermove", moveGesture, true)', script)
        self.assertIn("voiceRecordingStartedAt = 0", script)
        self.assertIn("voiceChunks = []", script)
        self.assertIn("stopImmediatePropagation", script)
        self.assertIn("Запись отменена", script)


if __name__ == "__main__":
    unittest.main()
