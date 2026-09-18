import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from flask import Flask, session

from handlers.voice import handle_voice
from integrations import speech


class SpeechIntegrationTests(unittest.TestCase):
    @patch("integrations.speech.requests.delete")
    @patch("integrations.speech.time.sleep", return_value=None)
    @patch("integrations.speech.time.monotonic", side_effect=[0, 0, 181])
    @patch("integrations.speech.requests.get")
    @patch("integrations.speech.requests.post")
    def test_transcription_times_out(self, post, get, monotonic, sleep, delete):
        upload = MagicMock()
        upload.json.return_value = {"upload_url": "https://example/audio"}
        transcript = MagicMock()
        transcript.json.return_value = {"id": "abc"}
        post.side_effect = [upload, transcript]

        pending = MagicMock()
        pending.json.return_value = {"status": "processing"}
        get.return_value = pending

        with tempfile.NamedTemporaryFile() as tmp:
            with patch.object(speech, "ASSEMBLYAI_API_KEY", "test-key"):
                with self.assertRaises(TimeoutError):
                    speech.transcribe_audio(tmp.name)

        delete.assert_called_once()

    @patch("integrations.speech.requests.delete")
    @patch("integrations.speech.requests.get")
    @patch("integrations.speech.requests.post")
    def test_completed_transcription_deletes_remote_artifacts(self, post, get, delete):
        upload = MagicMock()
        upload.json.return_value = {"upload_url": "https://example/audio"}
        transcript = MagicMock()
        transcript.json.return_value = {"id": "transcript-1"}
        post.side_effect = [upload, transcript]

        completed = MagicMock()
        completed.json.return_value = {"status": "completed", "text": "встреча завтра"}
        get.return_value = completed

        with tempfile.NamedTemporaryFile() as tmp:
            with patch.object(speech, "ASSEMBLYAI_API_KEY", "test-key"):
                result = speech.transcribe_audio(tmp.name)

        self.assertEqual(result, "встреча завтра")
        delete.assert_called_once_with(
            "https://api.assemblyai.com/v2/transcript/transcript-1",
            headers={"authorization": "test-key"},
            timeout=30,
        )

    def test_web_transcript_is_saved_as_text_only(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        with app.test_request_context("/"):
            session["user_id"] = 42
            with patch("core.ai_memory_store.record_ai_memory_event") as record:
                speech._store_web_transcript("купить билеты завтра")

        record.assert_called_once_with(
            42,
            "voice_transcript",
            0,
            "recognized",
            {"text": "купить билеты завтра", "source": "web_voice"},
        )

    def test_time_normalization(self):
        self.assertEqual(
            speech.normalize_time_format("встреча в 19.30"),
            "встреча в 19:30",
        )

    def test_missing_api_key_fails_before_network_request(self):
        with patch.object(speech, "ASSEMBLYAI_API_KEY", None):
            with patch("integrations.speech.requests.post") as post:
                with tempfile.NamedTemporaryFile() as tmp:
                    with self.assertRaises(RuntimeError):
                        speech.transcribe_audio(tmp.name)
                post.assert_not_called()


class TelegramVoiceAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_voice_routes_transcript_through_shared_assistant_flow(self):
        reply_text = AsyncMock()
        update = SimpleNamespace(
            message=SimpleNamespace(
                voice=SimpleNamespace(file_id="voice-1"),
                reply_text=reply_text,
            )
        )
        telegram_file = SimpleNamespace(download_to_drive=AsyncMock())
        context = SimpleNamespace(
            bot=SimpleNamespace(get_file=AsyncMock(return_value=telegram_file))
        )

        with patch("handlers.voice.transcribe_audio", return_value="встреча завтра в 19.30"):
            with patch("handlers.voice.handle_message_text", new=AsyncMock(return_value=True)) as shared:
                await handle_voice(update, context)

        shared.assert_awaited_once_with(update, context, "встреча завтра в 19:30")
        reply_text.assert_any_await("Распознано: встреча завтра в 19:30")

    async def test_english_voice_acknowledgement_is_localized(self):
        reply_text = AsyncMock()
        update = SimpleNamespace(
            message=SimpleNamespace(
                voice=SimpleNamespace(file_id="voice-en"),
                reply_text=reply_text,
            )
        )
        telegram_file = SimpleNamespace(download_to_drive=AsyncMock())
        context = SimpleNamespace(
            bot=SimpleNamespace(get_file=AsyncMock(return_value=telegram_file))
        )

        with patch("handlers.voice.transcribe_audio", return_value="meeting tomorrow at 7 pm"):
            with patch("handlers.voice.handle_message_text", new=AsyncMock(return_value=True)) as shared:
                await handle_voice(update, context)

        shared.assert_awaited_once_with(update, context, "meeting tomorrow at 7 pm")
        reply_text.assert_any_await("Recognized: meeting tomorrow at 7 pm")

    async def test_voice_does_not_keep_a_separate_unknown_command_fallback(self):
        reply_text = AsyncMock()
        update = SimpleNamespace(
            message=SimpleNamespace(
                voice=SimpleNamespace(file_id="voice-2"),
                reply_text=reply_text,
            )
        )
        telegram_file = SimpleNamespace(download_to_drive=AsyncMock())
        context = SimpleNamespace(
            bot=SimpleNamespace(get_file=AsyncMock(return_value=telegram_file))
        )

        with patch("handlers.voice.transcribe_audio", return_value="сделай что-нибудь"):
            with patch("handlers.voice.handle_message_text", new=AsyncMock(return_value=True)) as shared:
                await handle_voice(update, context)

        shared.assert_awaited_once_with(update, context, "сделай что-нибудь")
        reply_text.assert_any_await("Распознано: сделай что-нибудь")
        self.assertEqual(reply_text.await_count, 1)


if __name__ == "__main__":
    unittest.main()
