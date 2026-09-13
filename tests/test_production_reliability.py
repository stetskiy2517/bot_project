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

    def test_external_health_monitor_runs_every_fifteen_minutes(self):
        workflow = (ROOT / ".github" / "workflows" / "production-health.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "*/15 * * * *"', workflow)
        self.assertIn("PRODUCTION_HEALTH_URL", workflow)
        self.assertIn("/api/health", workflow)
        self.assertIn('payload.get("status") != "ok"', workflow)


if __name__ == "__main__":
    unittest.main()
