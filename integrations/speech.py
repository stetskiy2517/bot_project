"""Bilingual speech-to-text entrypoint.

The existing provider/fallback implementation lives in speech_core. AssemblyAI
is configured here for automatic Russian/English language detection while the
rest of the voice pipeline remains unchanged.
"""

from __future__ import annotations

from integrations import speech_core as _impl

# Preserve the public API and constants used by web/Telegram code and tests.
_CORE_EXPORTS = {name for name in dir(_impl) if not name.startswith("__")}
for _name in _CORE_EXPORTS:
    globals()[_name] = getattr(_impl, _name)

_WRAPPER_NAMES = {"_start_transcription", "_transcribe_assembly", "transcribe_audio", "speech_status"}


def _sync_runtime_overrides() -> None:
    """Keep monkey-patching integrations.speech compatible with the old module."""
    for name in _CORE_EXPORTS:
        if name in _WRAPPER_NAMES or name not in globals():
            continue
        value = globals()[name]
        if getattr(_impl, name, None) is not value:
            setattr(_impl, name, value)


def _start_transcription(audio_url: str) -> str:
    _sync_runtime_overrides()
    response = _impl.requests.post(
        f"{_impl.BASE_URL}/v2/transcript",
        headers={
            "authorization": _impl.ASSEMBLYAI_API_KEY,
            "content-type": "application/json",
        },
        json={
            "audio_url": audio_url,
            "speech_models": ["universal-3-pro", "universal-2"],
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


_original_transcribe_assembly = _impl._transcribe_assembly
_original_transcribe_audio = _impl.transcribe_audio
_original_speech_status = _impl.speech_status


def _transcribe_assembly(data: bytes) -> str:
    _sync_runtime_overrides()
    _impl._start_transcription = globals()["_start_transcription"]
    return _original_transcribe_assembly(data)


def transcribe_audio(source):
    _sync_runtime_overrides()
    _impl._start_transcription = globals()["_start_transcription"]
    _impl._transcribe_assembly = globals()["_transcribe_assembly"]
    return _original_transcribe_audio(source)


def speech_status() -> dict:
    _sync_runtime_overrides()
    return _original_speech_status()


_impl._start_transcription = _start_transcription
_impl._transcribe_assembly = _transcribe_assembly
