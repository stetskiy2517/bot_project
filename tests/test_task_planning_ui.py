from pathlib import Path
import unittest


class TaskPlanningUiTests(unittest.TestCase):
    def setUp(self):
        self.script = Path("web/tasks.js").read_text(encoding="utf-8")

    def test_planning_feedback_is_visible_inside_tasks_view(self):
        self.assertIn('plannerTaskFeedback', self.script)
        self.assertIn('planner-task-feedback', self.script)
        self.assertIn('skippedSummary(preview.skipped)', self.script)
        self.assertIn('нет свободного окна до срока', self.script)

    def test_task_edit_can_add_or_change_deadline(self):
        self.assertIn('promptDateTime(task.due_at)', self.script)
        self.assertIn('due_at: dueAt', self.script)
        self.assertIn('длительность задачи в минутах, минимум 5', self.script)


if __name__ == "__main__":
    unittest.main()
