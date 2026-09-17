from pathlib import Path
import unittest


class TaskPlanningUiTests(unittest.TestCase):
    def setUp(self):
        self.script = Path("web/tasks.js").read_text(encoding="utf-8")
        self.unified = Path("web/tasks-unified.js").read_text(encoding="utf-8")
        self.swipes = Path("web/task-swipe.js").read_text(encoding="utf-8")
        self.api = Path("modules/task_api.py").read_text(encoding="utf-8")
        self.worker = Path("web/sw.js").read_text(encoding="utf-8")

    def test_planning_feedback_is_visible_inside_tasks_view(self):
        self.assertIn('plannerTaskFeedback', self.script)
        self.assertIn('planner-task-feedback', self.script)
        self.assertIn('skippedSummary(preview.skipped)', self.script)
        self.assertIn('нет свободного окна до срока', self.script)

    def test_task_edit_can_add_or_change_deadline(self):
        self.assertIn('promptDateTime(task.due_at)', self.script)
        self.assertIn('due_at: dueAt', self.script)
        self.assertIn('длительность задачи в минутах, минимум 5', self.script)

    def test_reminders_are_presented_inside_tasks_not_as_a_separate_tab(self):
        self.assertIn('libraryRemindersTab', self.unified)
        self.assertIn('reminders.hidden = true', self.unified)
        self.assertIn('repeat(2, minmax(0, 1fr))', self.unified)
        self.assertIn('planner-reminder-task', self.unified)
        self.assertIn('Уведомление ${formatDate(item.remind_at)}', self.unified)
        self.assertIn('data-reminder-edit', self.unified)

    def test_reminder_engine_stays_available_behind_unified_tasks_view(self):
        self.assertIn('/api/library/reminders/${reminderId}/complete', self.unified)
        self.assertIn('/api/library/reminders/${reminderId}/reschedule', self.unified)
        self.assertIn('/api/mobile/reminders/details', self.unified)
        self.assertIn('<script defer src="/tasks.js"></script>', self.api)
        self.assertIn('<script defer src="/tasks-unified.js"></script>', self.api)
        self.assertIn('<script defer src="/task-swipe.js"></script>', self.api)
        self.assertIn('@task_api.get("/tasks-unified.js")', self.api)
        self.assertIn('@task_api.get("/task-swipe.js")', self.api)

    def test_pwa_shell_cannot_fall_back_to_pre_tasks_assets(self):
        self.assertIn('personal-secretary-v13-task-swipes', self.worker)
        self.assertIn('"/tasks.js"', self.worker)
        self.assertIn('"/tasks-unified.js"', self.worker)
        self.assertIn('"/task-swipe.js"', self.worker)
        self.assertIn('fetch(request, { cache: "no-store" })', self.worker)
        self.assertIn('cache.put(request, response.clone())', self.worker)
        self.assertIn('response.headers["Cache-Control"] = "no-store, max-age=0"', self.api)

    def test_task_cards_use_swipe_first_controls_and_notification_bell(self):
        self.assertIn('planner-task-swipe-row', self.swipes)
        self.assertIn('triggerPrimary', self.swipes)
        self.assertIn('revealActions', self.swipes)
        self.assertIn('planner-task-bell', self.swipes)
        self.assertIn('С уведомлением', self.swipes)
        self.assertIn('navigator.vibrate', self.swipes)
        self.assertIn('Смахните карточку вправо — выполнить. Влево — действия.', self.swipes)
        self.assertIn('Поиск по задачам', self.swipes)
        self.assertIn('notesProductToolbarWrap', self.swipes)


if __name__ == "__main__":
    unittest.main()
