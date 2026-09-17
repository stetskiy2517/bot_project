"""Bilingual speech-to-text entrypoint.

The existing provider/fallback implementation lives in speech_core. AssemblyAI
is configured here for automatic Russian/English language detection while the
rest of the voice pipeline remains unchanged.
"""

from __future__ import annotations

from integrations import speech_core as _impl

# Preserve the public API and constants used by web/Telegram code and tests.
for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)


def _start_transcription(audio_url: str) -> str:
    response = _impl.requests.post(
        f"{_impl.BASE_URL}/v2/transcript",
        headers={
            "authorization": _impl.ASSEMBLYAI_API_KEY,
            "content-type": "application/json",
        },
        json={
            "audio_url": audio_url,
            "speech_models": ["universal"],
            "language_detection": True,
            "language_detection_options": {
                "expected_languages": ["ru", "en"],
                "fallback_language": "auto",
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["id"]


# Preserve monkey-patching behavior expected by existing tests/integrations.
_original_transcribe_assembly = _impl._transcribe_assembly
_impl._start_transcription = _start_transcription


def _transcribe_assembly(data: bytes) -> str:
    _impl._start_transcription = globals()["_start_transcription"]
    return _original_transcribe_assembly(data)


def transcribe_audio(source):
    _impl._transcribe_assembly = globals()["_transcribe_assembly"]
    return _impl.transcribe_audio(source)
