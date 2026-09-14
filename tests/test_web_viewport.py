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


if __name__ == "__main__":
    unittest.main()
