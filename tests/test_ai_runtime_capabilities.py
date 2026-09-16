import unittest
from unittest.mock import patch

from modules.ai_assistant import _runtime_capabilities


class AIRuntimeCapabilitiesTests(unittest.TestCase):
    @patch("modules.ai_assistant.get_assistant_preferences", return_value={})
    @patch("modules.ai_assistant.navigation_configured", return_value=False)
    def test_tasks_are_reported_as_current_product_capability(self, _navigation, _preferences):
        capabilities = _runtime_capabilities(42)
        self.assertIn("календарь", capabilities)
        self.assertIn("напоминания", capabilities)
        self.assertIn("заметки", capabilities)
        self.assertIn("задачи", capabilities)


if __name__ == "__main__":
    unittest.main()
