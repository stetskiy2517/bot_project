from pathlib import Path
import unittest


class TaskPlanningUiTests(unittest.TestCase):
    def setUp(self):
        self.script = Path("web/tasks.js").read_text(encoding="utf-8")
        self.library = Path("web/library.js").read_text(encoding="utf-8")
        self.editor = Path("web/task-editor.js").read_text(encoding="utf-8")
        self.unified = Path("web/tasks-unified.js").read_text(encoding="utf-8")
        self.reminder_editor = Path("web/reminder-editor.js").read_text(encoding="utf-8")
        self.mobile = Path("web/mobile-ui.js").read_text(encoding="utf-8")
        self.swipes = Path("web/task-swipe.js").read_text(encoding="utf-8")
        self.api = Path("modules/task_api.py").read_text(encoding="utf-8")
        self.worker = Path("web/sw.js").read_text(encoding="utf-8")
        self.prebeta = Path("web/prebeta-polish.js").read_text(encoding="utf-8")
        self.ux = Path("web/ux-polish.js").read_text(encoding="utf-8")

    def test_planning_feedback_is_visible_inside_tasks_view(self):
        self.assertIn("plannerTaskFeedback", self.script)
        self.assertIn("planner-task-feedback", self.script)
        self.assertIn("skippedSummary(preview.skipped)", self.script)
        self.assertIn("нет свободного окна до срока", self.script)

    def test_tasks_use_native_editor_not_browser_prompts(self):
        self.assertNotIn("prompt(", self.script)
        self.assertNotIn("confirm(", self.script)
        self.assertNotIn("prompt(", self.unified)
        self.assertNotIn("confirm(", self.unified)
        self.assertNotIn("confirm(", self.mobile)
        self.assertIn("PlannerTaskEditor", self.script)
        self.assertIn("PlannerTaskEditor.confirmPlan", self.mobile)
        self.assertIn('id="taskEditDescription"', self.editor)
        self.assertIn('id="taskEditDueDate"', self.editor)
        self.assertIn('id="taskEditDueTime"', self.editor)
        self.assertIn('id="taskEditEstimate"', self.editor)
        self.assertIn("height:100%", self.editor)
        self.assertIn('aria-label="Назад"', self.editor)
        self.assertIn('id="taskEditCategory"', self.editor)
        self.assertIn('id="taskEditPriority"', self.editor)
        self.assertIn('id="taskEditRepeat"', self.editor)
        self.assertIn("offerUndo", self.editor)
        self.assertIn("confirmPlan", self.editor)
        self.assertIn("grid-template-columns:minmax(0,1.2fr)", self.editor)
        self.assertIn('input[type="date"]', self.editor)
        self.assertIn("max-inline-size:100%", self.editor)

    def test_notification_tasks_edit_time_in_same_sheet(self):
        self.assertIn('id="reminderEditDate"', self.reminder_editor)
        self.assertIn('id="reminderEditTime"', self.reminder_editor)
        self.assertIn("if (timeChanged) update.remind_at = at.toISOString()", self.reminder_editor)
        self.assertIn("height:100%", self.reminder_editor)
        self.assertIn("PlannerReminderEditor.open(reminderId, {focusTime: true})", self.unified)

    def test_saved_screen_uses_compact_single_row_header_and_tasks_are_first(self):
        self.assertNotIn('<h2 class="library-title">Сохранённое</h2>', self.library)
        self.assertNotIn('class="library-title"', self.library)
        self.assertIn('id="libraryNavActions"', self.library)
        self.assertIn("grid-template-columns: 44px minmax(0,1fr) auto", self.library)
        self.assertIn('tabs.insertBefore(button, notes);', self.script)

    def test_task_actions_are_compact_header_icons_with_expandable_search(self):
        self.assertIn('root.id = "plannerTaskHeaderActions"', self.script)
        self.assertIn('id="plannerTaskSearchBtn"', self.script)
        self.assertIn('id="plannerTaskPlanBtn"', self.script)
        self.assertIn('id="plannerTaskCreateBtn"', self.script)
        self.assertIn('id="plannerTaskSearchInput"', self.script)
        self.assertIn("planner-task-search-shell.open .planner-task-search-input", self.script)
        self.assertIn("task-search-open", self.script)
        self.assertIn("closeTaskSearch", self.script)
        self.assertIn("resetTaskSearch", self.script)
        self.assertIn('taskQuery = ""', self.script)
        self.assertIn("taskSearchObserved", self.script)
        self.assertIn('!app.classList.contains("library-active")', self.script)
        self.assertIn("transition:width .24s", self.script)
        self.assertIn("filters.append(category, priority)", self.script)
        self.assertNotIn('create.textContent = "+ Задача"', self.script)

    def test_reminders_are_presented_inside_tasks_not_as_a_separate_tab(self):
        self.assertIn("libraryRemindersTab", self.unified)
        self.assertIn("reminders.hidden = true", self.unified)
        self.assertIn("repeat(2, minmax(0, 1fr))", self.unified)
        self.assertIn("planner-reminder-task", self.unified)
        self.assertIn("Уведомление ${formatDate(item.scheduled_at || item.remind_at)}", self.unified)
        self.assertIn("data-reminder-edit", self.unified)
        self.assertIn("updateTaskSummary", self.unified)
        self.assertIn("· С уведомлением ${count}", self.unified)
        self.assertNotIn("planner-task-reminder-summary", self.unified)

    def test_reminder_engine_stays_available_behind_unified_tasks_view(self):
        self.assertIn("/api/library/reminders/${reminderId}/complete", self.unified)
        self.assertIn("/api/mobile/reminders/details", self.unified)
        self.assertIn("/api/mobile/reminders/${Number(reminderId)}/details", self.reminder_editor)
        self.assertIn('<script defer src="/task-editor.js"></script>', self.api)
        self.assertIn('<script defer src="/tasks.js"></script>', self.api)
        self.assertIn('<script defer src="/tasks-unified.js"></script>', self.api)
        self.assertIn('<script defer src="/task-swipe.js"></script>', self.api)
        self.assertIn('@task_api.get("/task-editor.js")', self.api)

    def test_pwa_shell_uses_controlled_prebeta_update(self):
        self.assertIn("personal-secretary-v21-theme-stability", self.worker)
        for asset in ("/task-editor.js", "/tasks.js", "/tasks-unified.js", "/task-swipe.js", "/prebeta-polish.js", "/ux-polish.js"):
            self.assertIn(f'"{asset}"', self.worker)
        install_block = self.worker.split('self.addEventListener("install"', 1)[1].split('self.addEventListener("message"', 1)[0]
        self.assertNotIn("skipWaiting", install_block)
        self.assertIn('event.data?.type === "SKIP_WAITING"', self.worker)
        self.assertIn("self.skipWaiting()", self.worker)
        self.assertIn('fetch(request, {cache: "no-store"})', self.worker)
        self.assertIn("cache.put(request, response.clone())", self.worker)
        self.assertIn('response.headers["Cache-Control"] = "no-store, max-age=0"', self.api)
        self.assertIn("Доступно обновление приложения", self.prebeta)
        self.assertIn("UPDATE_PENDING_KEY", self.prebeta)

    def test_task_cards_use_swipe_first_controls_and_notification_bell(self):
        self.assertIn("planner-task-swipe-row", self.swipes)
        self.assertIn("triggerPrimary", self.swipes)
        self.assertIn("revealActions", self.swipes)
        self.assertIn("planner-task-bell", self.swipes)
        self.assertIn("Настроить уведомление", self.swipes)
        self.assertIn("navigator.vibrate", self.swipes)
        self.assertIn("Смахните карточку вправо — выполнить. Влево — действия.", self.swipes)
        self.assertIn("Поиск по задачам", self.swipes)
        self.assertIn("notesProductToolbarWrap", self.swipes)
        self.assertIn('card.addEventListener("click", openEditor)', self.script)
        self.assertIn('event.target.closest(".planner-reminder-task")', self.unified)

    def test_prebeta_reliability_and_polish_are_present(self):
        self.assertIn("Нет соединения", self.prebeta)
        self.assertIn("Соединение восстановлено", self.prebeta)
        self.assertIn("friendlyError", self.prebeta)
        self.assertIn("diagnosticsGroup", self.prebeta)
        self.assertIn("copyDiagnostics", self.prebeta)
        self.assertIn("settings-theme-flat-group", self.ux)
        self.assertIn("Заметок пока нет", self.ux)
        self.assertIn("В календаре свободно", self.ux)


if __name__ == "__main__":
    unittest.main()
