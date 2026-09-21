import unittest

import web_app


class SettingsUiGroupsTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app()
        self.client = self.app.test_client()

    def test_calendar_and_dynamic_settings_are_compacted_into_groups(self):
        response = self.client.get("/reliability.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()

        self.assertIn('group.id = "calendarSettingsGroup"', script)
        self.assertIn('decorateSummary(group, "Календарь"', script)
        self.assertIn('group.id = "notificationsGroup"', script)
        self.assertIn('decorateSummary(group, "Уведомления"', script)
        self.assertIn('"Обзоры и тихие часы", "Расписание"', script)
        self.assertIn('"Избранные команды", "Шаблоны"', script)
        self.assertIn('"Отмена и данные", "Данные аккаунта"', script)
        self.assertIn('details.classList.add("settings-group")', script)
        self.assertIn("new MutationObserver", script)

    def test_proactive_assistant_uses_standard_settings_group_markup(self):
        response = self.client.get("/proactive.js")
        self.assertEqual(response.status_code, 200)
        script = response.get_data(as_text=True)
        response.close()

        self.assertIn('section.className = "assistant-section settings-group"', script)
        self.assertIn('class="settings-group-summary-ready"', script)
        self.assertIn('class="settings-group-title">Проактивный помощник', script)
        self.assertIn('class="settings-group-meta">ИИ', script)
        self.assertIn('class="settings-group-chevron"', script)

    def test_existing_setting_controls_keep_their_ids(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        response.close()

        for control_id in (
            "workStart",
            "workEnd",
            "buffer",
            "navigationGroup",
            "categoryColorsGroup",
        ):
            self.assertIn(f'id="{control_id}"', html)

        self.assertNotIn('id="saveSettings"', html)
        self.assertIn("scheduleSettingsSave", html)
        self.assertNotIn('id="timezone"', html)

    def test_settings_modules_use_autosave_instead_of_save_buttons(self):
        for path, removed_id, autosave_marker in (
            ("/assistant.js", "saveAssistantDelivery", "scheduleDeliverySave"),
            ("/proactive.js", "saveProactiveActions", "scheduleSave"),
            ("/navigation-extra.js", "saveNavigationExtra", "scheduleBufferSave"),
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            script = response.get_data(as_text=True)
            response.close()
            self.assertNotIn(removed_id, script)
            self.assertIn(autosave_marker, script)


if __name__ == "__main__":
    unittest.main()
