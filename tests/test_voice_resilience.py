import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from handlers.voice import handle_voice
from integrations import speech


class SpeechIntegrationTests(unittest.TestCase):
    @patch("integrations.speech.time.sleep", return_value=None)
    @patch("integrations.speech.time.monotonic", side_effect=[0, 0, 181])
    @patch("integrations.speech.requests.get")
    @patch("integrations.speech.requests.post")
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
            with patch.object(speech, "ASSEMBLYAI_API_KEY", "test-key"):
                with self.assertRaises(TimeoutError):
                    speech.transcribe_audio(tmp.name)

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
    async def test_telegram_voice_routes_transcript_through_central_router(self):
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
            with patch("handlers.voice.route_text", new=AsyncMock(return_value=True)) as route:
                await handle_voice(update, context)

        route.assert_awaited_once_with(update, context, text="встреча завтра в 19:30")
        reply_text.assert_any_await("Распознано: встреча завтра в 19:30")

    async def test_unknown_voice_command_uses_transport_neutral_fallback(self):
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
            with patch("handlers.voice.route_text", new=AsyncMock(return_value=False)):
                await handle_voice(update, context)

        reply_text.assert_any_await(
            "Не понял команду. Скажи иначе или уточни, что нужно сделать."
        )


if __name__ == "__main__":
    unittest.main()
