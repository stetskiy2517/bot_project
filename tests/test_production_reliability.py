from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProductionReliabilityWiringTests(unittest.TestCase):
    def test_automatic_deploy_creates_backup_and_checks_reboot_units_without_new_sudo_rights(self):
        script = (ROOT / "scripts" / "deploy_update.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/backup_state.py", script)
        self.assertIn('systemctl is-enabled --quiet "$SERVICE_NAME"', script)
        self.assertIn("systemctl is-enabled --quiet caddy", script)
        self.assertNotIn('sudo -n systemctl enable', script)
        self.assertNotIn("install_backup_timer.sh", script)

    def test_full_deploy_creates_verified_backup(self):
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/backup_state.py", script)
        self.assertIn('systemctl is-enabled --quiet "$SERVICE_NAME"', script)
        self.assertIn("systemctl is-enabled --quiet caddy", script)
        self.assertNotIn("personal-secretary-backup.timer", script)
        self.assertNotIn("install_backup_timer.sh", script)

    def test_daily_backup_uses_existing_deploy_ssh_connection(self):
        workflow = (ROOT / ".github" / "workflows" / "production-backup.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "25 1 * * *"', workflow)
        self.assertIn("DEPLOY_HOST", workflow)
        self.assertIn("DEPLOY_USER", workflow)
        self.assertIn("DEPLOY_SSH_KEY", workflow)
        self.assertIn("DEPLOY_KNOWN_HOSTS", workflow)
        self.assertIn("scripts/backup_state.py", workflow)
        self.assertNotIn("sudo ", workflow)

    def test_disk_maintenance_runs_after_deploy_and_on_schedule(self):
        workflow = (ROOT / ".github" / "workflows" / "production-disk-maintenance.yml").read_text(encoding="utf-8")
        script = (ROOT / "scripts" / "disk_maintenance.sh").read_text(encoding="utf-8")
        self.assertIn('cron: "45 1 * * *"', workflow)
        self.assertIn("workflow_run:", workflow)
        self.assertIn("- deploy-production", workflow)
        self.assertIn("scripts/disk_maintenance.sh", workflow)
        self.assertIn("--max-disk-percent 90", workflow)
        self.assertIn("scripts/backup_state.py --prune-only", script)
        self.assertIn('rm -rf -- "$HOME/.cache/pip"', script)
        self.assertIn('journalctl --vacuum-size="$JOURNAL_LIMIT"', script)
        self.assertIn("system prune -af", script)
        self.assertNotIn("--volumes", script)

    def test_external_health_monitor_runs_on_schedule_and_after_successful_deploy(self):
        workflow = (ROOT / ".github" / "workflows" / "production-health.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "*/15 * * * *"', workflow)
        self.assertIn("workflow_run:", workflow)
        self.assertIn("- deploy-production", workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", workflow)
        self.assertIn("github.event.workflow_run.head_sha || github.sha", workflow)
        self.assertIn("PRODUCTION_HEALTH_URL", workflow)
        self.assertIn("/api/health", workflow)
        self.assertIn('payload.get("status") != "ok"', workflow)


if __name__ == "__main__":
    unittest.main()
