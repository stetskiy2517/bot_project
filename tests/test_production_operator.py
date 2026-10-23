"""Tests for the encrypted, constrained VM operator channel."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

from scripts import production_operator as operator


class ProductionOperatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key_dir = Path(self.temp.name) / "operator"
        self.key_path = self.key_dir / "private.pem"
        self.patches = (
            patch.object(operator, "KEY_DIR", self.key_dir),
            patch.object(operator, "KEY_PATH", self.key_path),
        )
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def encrypt_request(self, payload):
        request_id = payload["request_id"]
        secret = os.urandom(32)
        nonce = os.urandom(12)
        public = operator._private_key().public_key()
        ciphertext = AESGCM(secret).encrypt(
            nonce, json.dumps(payload).encode(), request_id.encode(),
        )
        encrypted_key = public.encrypt(
            secret,
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        envelope = {
            "version": 1,
            "request_id": request_id,
            "wrapped_key": base64.b64encode(encrypted_key).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
        }
        path = Path(self.temp.name) / "request.json"
        path.write_text(json.dumps(envelope))
        return path

    def test_private_key_persists_and_is_private(self):
        first = operator._private_key()
        second = operator._private_key()
        self.assertEqual(
            first.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo),
            second.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo),
        )
        self.assertEqual(self.key_path.stat().st_mode & 0o077, 0)

    def test_decrypts_only_valid_encrypted_request(self):
        data = {"request_id": "safe-op-123", "action": "health"}
        path = self.encrypt_request(data)
        self.assertEqual(operator.decrypt_request(path), (data["request_id"], data))

    def test_nonce_and_id_are_authenticated(self):
        data = {"request_id": "safe-op-124", "action": "health"}
        path = self.encrypt_request(data)
        content = json.loads(path.read_text())
        content["request_id"] = "tampered-id-124"
        path.write_text(json.dumps(content))
        with self.assertRaises(Exception):
            operator.decrypt_request(path)

    def test_operator_rejects_unknown_action(self):
        payload = {"request_id": "safe-op-125", "action": "shell", "command": "echo no"}
        path = self.encrypt_request(payload)
        with self.assertRaises(ValueError):
            operator.run_request(path)


if __name__ == "__main__":
    unittest.main()
