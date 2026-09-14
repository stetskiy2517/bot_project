import unittest

import web_app


class WebViewportTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()

    def test_mobile_shell_is_locked_to_visual_viewport(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        response.close()

        self.assertIn("maximum-scale=1", html)
        self.assertIn("user-scalable=no", html)
        self.assertIn("interactive-widget=resizes-content", html)
        self.assertIn("--app-height: 100dvh", html)
        self.assertIn("height: var(--app-height, 100dvh)", html)
        self.assertIn("function syncViewportHeight()", html)
        self.assertIn("window.visualViewport", html)
        self.assertIn('window.addEventListener("orientationchange"', html)

    def test_mobile_form_controls_do_not_trigger_ios_focus_zoom(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        response.close()

        self.assertIn("@media (max-width: 759px)", html)
        self.assertIn(".composer input,", html)
        self.assertIn(".field input,", html)
        self.assertIn(".field select", html)
        self.assertIn("font-size: 16px", html)

    def test_keyboard_keeps_chat_top_anchored_and_only_raises_bottom_edge(self):
        response = self.client.get("/reliability.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()

        self.assertIn("installStableMobileViewport", script)
        self.assertIn("--stable-app-height", script)
        self.assertIn("--keyboard-inset", script)
        self.assertIn(".chat-shell", script)
        self.assertIn(".composer-wrap", script)
        self.assertIn("stableHeight - visualBottom", script)
        self.assertIn("keyboardOpen = focused && hiddenBottom > 100", script)
        self.assertIn('document.activeElement?.id === "message"', script)
        self.assertIn("chat.scrollTop = chat.scrollHeight", script)

    def test_stable_viewport_fix_is_loaded_before_inline_chat_code(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        response.close()

        reliability = html.find('<script src="/reliability.js"></script>')
        inline_chat = html.find("const app = document.getElementById")
        self.assertGreaterEqual(reliability, 0)
        self.assertGreater(inline_chat, reliability)


if __name__ == "__main__":
    unittest.main()
