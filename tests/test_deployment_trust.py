"""Deployment trust failures must be actionable and must not invoke inference."""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from integrations import ai
from scripts import preflight_trust


class DeploymentTrustTests(unittest.TestCase):
    def setUp(self):
        self.settings = ai.AISettings(
            True, "gigachat", "model", "private-credential-do-not-print", "scope",
            "https://api.example", "https://auth.example", 5, 100, None,
        )

    def test_unconfigured_trust_refuses_deployment_without_exposing_credential(self):
        output = StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(ai, "load_ai_settings", return_value=self.settings), patch.object(ai, "DEFAULT_GIGACHAT_CA_BUNDLE", Path(temporary)/"bundle.pem"), patch.object(ai, "DEFAULT_GIGACHAT_ROOT_CERT", Path(temporary)/"root.pem"), patch.object(ai, "complete") as inference, redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(preflight_trust.main(), 1)
                inference.assert_not_called()
        self.assertIn("GIGACHAT_ROOT_SHA256", output.getvalue())
        self.assertNotIn(self.settings.credentials, output.getvalue())

    def test_invalid_operator_bundle_refuses_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)/"invalid.pem"
            bundle.write_text("not a certificate")
            with patch.object(ai, "load_ai_settings", return_value=replace(self.settings, ca_bundle=str(bundle))), redirect_stderr(StringIO()):
                self.assertEqual(preflight_trust.main(), 1)

    def test_valid_operator_bundle_passes_without_inference(self):
        with patch.object(ai, "load_ai_settings", return_value=replace(self.settings, ca_bundle=ai.certifi.where())), patch.object(ai, "complete") as inference, redirect_stdout(StringIO()):
            self.assertEqual(preflight_trust.main(), 0)
            inference.assert_not_called()

    def test_disabled_ai_does_not_block_deterministic_product(self):
        with patch.object(ai, "load_ai_settings", return_value=replace(self.settings, enabled=False)), patch.object(ai, "_ensure_gigachat_ca_bundle") as ensure, redirect_stdout(StringIO()):
            self.assertEqual(preflight_trust.main(), 0)
            ensure.assert_not_called()
