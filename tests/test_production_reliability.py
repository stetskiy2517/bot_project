from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProductionReliabilityWiringTests(unittest.TestCase):
    def test_backup_timer_is_persistent_and_runs_daily(self):
        script = (ROOT / "scripts" / "install_backup_timer.sh").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* 03:15:00", script)
        self.assertIn("Persistent=true", script)
        self.assertIn("systemctl enable --now", script)
        self.assertIn("systemctl start", script)
        self.assertIn("backup_state.py", script)

    def test_automatic_deploy_reinstalls_backup_timer_and_checks_reboot_units(self):
        script = (ROOT / "scripts" / "deploy_update.sh").read_text(encoding="utf-8")
        self.assertIn("install_backup_timer.sh", script)
        self.assertIn('systemctl enable "$SERVICE_NAME"', script)
        self.assertIn("systemctl enable caddy", script)
        self.assertIn('systemctl is-enabled --quiet "$SERVICE_NAME"', script)
        self.assertIn("systemctl is-enabled --quiet caddy", script)

    def test_full_deploy_installs_backup_timer(self):
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("install_backup_timer.sh", script)
        self.assertIn("personal-secretary-backup.timer", script)

    def test_external_health_monitor_runs_every_fifteen_minutes(self):
        workflow = (ROOT / ".github" / "workflows" / "production-health.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "*/15 * * * *"', workflow)
        self.assertIn("PRODUCTION_HEALTH_URL", workflow)
        self.assertIn("/api/health", workflow)
        self.assertIn('payload.get("status") != "ok"', workflow)


if __name__ == "__main__":
    unittest.main()
