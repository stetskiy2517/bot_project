import tempfile
import unittest
from unittest.mock import MagicMock, patch

from handlers import voice


class VoiceResilienceTests(unittest.TestCase):
    @patch("handlers.voice.time.sleep", return_value=None)
    @patch("handlers.voice.time.monotonic", side_effect=[0, 0, 181])
    @patch("handlers.voice.requests.get")
    @patch("handlers.voice.requests.post")
    def test_transcription_times_out(self, post, get, monotonic, sleep):
        upload = MagicMock()
        upload.json.return_value = {"upload_url": "https://example/audio"}
        transcript = MagicMock()
        transcript.json.return_value = {"id": "abc"}
        post.side_effect = [upload, transcript]

        pending = MagicMock()
        pending.json.return_value = {"status": "processing"}
        get.return_value = pending

        with tempfile.NamedTemporaryFile() as tmp:
            with patch.object(voice, "ASSEMBLYAI_API_KEY", "test-key"):
                with self.assertRaises(TimeoutError):
                    voice.transcribe_audio(tmp.name)

    def test_time_normalization(self):
        self.assertEqual(voice.normalize_time_format("встреча в 19.30"), "встреча в 19:30")


if __name__ == "__main__":
    unittest.main()
