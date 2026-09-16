import hashlib
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import uuid

import web_app
from core.db import get_or_create_google_user

ROOT = Path(__file__).resolve().parents[1]


class ReleaseIntegrationTests(unittest.TestCase):
    def test_security_dependency_pins_survive_integration(self):
        requirements = (ROOT / "requirements.txt").read_text()
        self.assertIn("Flask==3.1.3", requirements)
        self.assertIn("python-dotenv==1.2.2", requirements)

    def test_unrelated_oauth_callback_does_not_destroy_valid_login(self):
        app = web_app.create_web_app()
        client = app.test_client()
        state = uuid.uuid4().hex
        user = get_or_create_google_user(uuid.uuid4().hex, 'release@example.test', 'Release')
        with client.session_transaction() as session:
            session['oauth_binding'] = {
                'digest': hashlib.sha256(state.encode()).hexdigest(), 'issued_at': time.time(),
            }
        with patch.object(web_app, 'complete_web_signin', return_value=user) as exchange:
            bad = client.get('/oauth2callback', query_string={'state': 'unrelated', 'code': 'bad'})
            self.assertEqual(bad.status_code, 400)
            exchange.assert_not_called()
            good = client.get('/oauth2callback', query_string={'state': state, 'code': 'good'})
            self.assertEqual(good.status_code, 302)
            exchange.assert_called_once_with(state, 'good')

    def test_predeployment_backup_precedes_code_and_config_update(self):
        script = (ROOT / 'scripts/deploy_update.sh').read_text()
        preflight = script.index('log "Creating verified pre-deployment backup"')
        deploy = script.index('log "Deploying commit $TARGET_SHA"')
        self.assertLess(preflight, deploy)
        self.assertIn('install -m 600 .env "$PREDEPLOY_BACKUP/environment.env"', script)
        self.assertIn('install -m 600 "$PREDEPLOY_BACKUP/environment.env" .env', script)

    def test_browser_tests_are_part_of_deployment_gate(self):
        workflow = (ROOT / '.github/workflows/tests.yml').read_text()
        self.assertIn('uses: ./.github/workflows/browser-audit.yml', workflow)
        browser = (ROOT / '.github/workflows/browser-audit.yml').read_text()
        self.assertIn('workflow_call:', browser)
        self.assertIn('python tests/browser_features.py --output', browser)
        self.assertNotIn('browser_features.py --offline', browser)

        def step_block(name: str) -> str:
            marker = f'      - name: {name}\n'
            start = browser.index(marker)
            end = browser.find('\n      - name:', start + len(marker))
            return browser[start:] if end < 0 else browser[start:end]

        # Test execution remains a hard deployment gate. Only best-effort evidence
        # upload may fail without blocking a tested release.
        for name in (
            'Run browser feature regression',
            'Test home composer and file import',
            'Test email settings initialization',
            'Test attention center',
            'Test reminder editor',
            'Run WebKit iPhone smoke',
        ):
            self.assertNotIn('continue-on-error', step_block(name), name)
        self.assertIn('continue-on-error: true', step_block('Upload browser evidence'))
        self.assertIn('continue-on-error: true', step_block('Upload WebKit evidence'))

    def test_python_matrix_includes_server_compatible_runtime(self):
        workflow = (ROOT / '.github/workflows/tests.yml').read_text()
        self.assertIn("python: ['3.10', '3.11', '3.13']", workflow)

    def test_navigation_and_viewport_from_main_are_preserved(self):
        self.assertTrue((ROOT / 'integrations/navigation_ors.py').is_file())
        html = (ROOT / 'web/index.html').read_text()
        current = (ROOT / 'config.py').read_text()
        self.assertIn('ORS_API_KEY', current)
        self.assertIn('visualViewport', html)


if __name__ == '__main__':
    unittest.main()
