from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.db import conn, db_lock
from core.identity_store import get_identity_account, get_or_create_identity_user
from core.life_balance_store import get_life_balance_ratings, save_life_balance_rating
from core.note_enhancements import enhanced_note, update_note_content, update_note_metadata
from core.note_store import create_note
from integrations.speech import transcribe_audio


class IdentityStoreTests(unittest.TestCase):
    def setUp(self):
        self.subject = "upgrade-yandex-identity-test"
        with db_lock:
            row = conn.execute(
                "SELECT user_id FROM identity_accounts WHERE provider='yandex' AND subject=?",
                (self.subject,),
            ).fetchone()
            if row:
                self._cleanup(int(row[0]))

    def tearDown(self):
        with db_lock:
            row = conn.execute(
                "SELECT user_id FROM identity_accounts WHERE provider='yandex' AND subject=?",
                (self.subject,),
            ).fetchone()
            if row:
                self._cleanup(int(row[0]))

    def _cleanup(self, user_id: int):
        conn.execute("DELETE FROM identity_accounts WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM google_accounts WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        conn.commit()

    def test_yandex_identity_is_stable_and_does_not_create_google_token(self):
        first = get_or_create_identity_user("yandex", self.subject, "identity@example.test", "Yandex User")
        second = get_or_create_identity_user("yandex", self.subject, "new@example.test", "Updated User")
        self.assertEqual(first, second)
        account = get_identity_account(first)
        self.assertEqual(account["provider"], "yandex")
        self.assertEqual(account["email"], "new@example.test")
        with db_lock:
            token = conn.execute("SELECT google_token FROM users WHERE user_id=?", (first,)).fetchone()[0]
        self.assertIsNone(token)


class NoteEnhancementTests(unittest.TestCase):
    USER_ID = 86753101

    def tearDown(self):
        with db_lock:
            note_ids = [row[0] for row in conn.execute("SELECT note_id FROM notes WHERE user_id=?", (self.USER_ID,)).fetchall()]
            for note_id in note_ids:
                conn.execute("DELETE FROM note_metadata WHERE user_id=? AND note_id=?", (self.USER_ID, note_id))
                conn.execute("DELETE FROM ai_memory_events WHERE user_id=? AND entity_type='note' AND entity_id=?", (self.USER_ID, note_id))
            conn.execute("DELETE FROM notes WHERE user_id=?", (self.USER_ID,))
            conn.commit()

    def test_note_can_be_edited_tagged_pinned_and_checklisted(self):
        note = create_note(self.USER_ID, "Старый текст", title="Старая заметка")
        updated = update_note_content(self.USER_ID, note["note_id"], title="Новая заметка", text="Новый текст")
        self.assertEqual(updated["title"], "Новая заметка")
        metadata = update_note_metadata(
            self.USER_ID,
            note["note_id"],
            pinned=True,
            tags=["машина", "личное", "машина"],
            checklist=[{"text": "Купить масло", "done": False}],
        )
        self.assertTrue(metadata["pinned"])
        self.assertEqual(metadata["tags"], ["машина", "личное"])
        self.assertEqual(metadata["checklist"][0]["text"], "Купить масло")
        loaded = enhanced_note(self.USER_ID, note["note_id"])
        self.assertEqual(loaded["text"], "Новый текст")


class LifeBalanceStoreTests(unittest.TestCase):
    USER_ID = 86753102

    def tearDown(self):
        with db_lock:
            conn.execute("DELETE FROM life_balance_ratings WHERE user_id=?", (self.USER_ID,))
            conn.commit()

    def test_subjective_rating_is_separate_from_target(self):
        saved = save_life_balance_rating(self.USER_ID, "health", rating=4.5, target=8)
        self.assertEqual(saved["rating"], 4.5)
        self.assertEqual(saved["target"], 8.0)
        loaded = get_life_balance_ratings(self.USER_ID)["health"]
        self.assertEqual(loaded["rating"], 4.5)
        self.assertEqual(loaded["target"], 8.0)


class SpeechProviderTests(unittest.TestCase):
    def test_yandex_fallback_is_used_after_assembly_failure_for_ogg(self):
        audio = b"OggS" + b"test-audio"
        with patch.dict(
            os.environ,
            {
                "SPEECH_PROVIDER_ORDER": "assemblyai,yandex",
                "YANDEX_SPEECHKIT_API_KEY": "test-key",
            },
            clear=False,
        ), patch("integrations.speech.ASSEMBLYAI_API_KEY", "assembly-key"), patch(
            "integrations.speech._transcribe_assembly", side_effect=RuntimeError("provider down")
        ), patch("integrations.speech.yandex_configured", return_value=True), patch(
            "integrations.speech.recognize_oggopus", return_value="встреча завтра в 15"
        ), patch("integrations.speech._store_web_transcript"):
            text = transcribe_audio(__import__("io").BytesIO(audio))
        self.assertEqual(text, "встреча завтра в 15")


if __name__ == "__main__":
    unittest.main()
