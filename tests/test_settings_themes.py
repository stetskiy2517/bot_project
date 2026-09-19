import unittest

import web_app


class SettingsThemeUiTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()

    def test_root_loads_settings_theme_asset(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        response.close()
        self.assertIn('<script src="/settings-themes.js"></script>', html)

        response = self.client.get("/settings-themes.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()
        self.assertIn('title: "Планирование"', script)
        self.assertIn('title: "Уведомления"', script)
        self.assertIn('title: "Ассистент"', script)
        self.assertIn('title: "Интеграции"', script)
        self.assertIn('title: "Аккаунт и данные"', script)

    def test_existing_setting_groups_are_routed_by_topic(self):
        response = self.client.get("/settings-themes.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()

        self.assertIn('["calendarSettingsGroup", "navigationGroup", "categoryColorsGroup"]', script)
        self.assertIn('id === "notificationsGroup"', script)
        self.assertIn('id === "emailGroup"', script)
        self.assertIn('group.querySelector("#proactiveRemindersEnabled")', script)
        self.assertIn('group.querySelector("#assistantMemoryList")', script)
        self.assertIn('group.querySelector("#templateForm")', script)
        self.assertIn('group.querySelector("#eraseAccount")', script)

    def test_only_account_action_is_moved_to_relevant_theme(self):
        response = self.client.get("/settings-themes.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()

        self.assertNotIn('planningThemeActions', script)
        self.assertNotIn('saveSettings', script)
        self.assertIn('accountThemeActions', script)
        self.assertIn('actions.appendChild(logout)', script)


if __name__ == "__main__":
    unittest.main()
