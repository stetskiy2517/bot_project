"""Speech-to-text integration shared by web and Telegram transports."""

from __future__ import annotations

from io import BytesIO
import logging
import os
import re
import time
from pathlib import Path
from typing import BinaryIO

import requests
from dotenv import load_dotenv

from integrations.speech_yandex import YandexSpeechError, configured as yandex_configured, recognize_oggopus

load_dotenv()

logger = logging.getLogger(__name__)
ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
BASE_URL = "https://api.assemblyai.com"
TRANSCRIPTION_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2
EXPLICIT_DOTTED_TIME_RE = re.compile(
    r"\b(?P<prefix>(?:в|к|с|до)\s+)(?P<hour>[01]?\d|2[0-3])"
    r"\.(?P<minute>[0-5]\d)(?!\d|\.\d)",
    re.IGNORECASE,
)
VOICE_MEETING_CLIENT_ASR_RE = re.compile(
    r"^(?P<prefix>\s*)(?P<word>встречи)(?=\s+с\s+клиентом\b)",
    re.IGNORECASE,
)


def _normalize_meeting_client_asr(text: str) -> str:
    def replace(match: re.Match) -> str:
        word = match.group("word")
        replacement = "Встреча" if word[:1].isupper() else "встреча"
        return f"{match.group('prefix')}{replacement}"
    return VOICE_MEETING_CLIENT_ASR_RE.sub(replace, text, count=1)


def normalize_time_format(text: str) -> str:
    normalized = EXPLICIT_DOTTED_TIME_RE.sub(r"\g<prefix>\g<hour>:\g<minute>", text)
    return _normalize_meeting_client_asr(normalized)


def _upload_audio(audio: BinaryIO) -> str:
    try:
        audio.seek(0)
    except (AttributeError, OSError):
        pass
    response = requests.post(
        f"{BASE_URL}/v2/upload",
        headers={"authorization": ASSEMBLYAI_API_KEY},
        data=audio,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["upload_url"]


def _start_transcription(audio_url: str) -> str:
    response = requests.post(
        f"{BASE_URL}/v2/transcript",
        headers={
            "authorization": ASSEMBLYAI_API_KEY,
            "content-type": "application/json",
        },
        json={
            "audio_url": audio_url,
            "language_code": "ru",
            "speech_model": "universal",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["id"]


def _wait_for_transcript(transcript_id: str) -> str:
    deadline = time.monotonic() + TRANSCRIPTION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        response = requests.get(
            f"{BASE_URL}/v2/transcript/{transcript_id}",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        status = result.get("status")
        if status == "completed":
            text = result.get("text")
            return text.strip() if isinstance(text, str) else ""
        if status == "error":
            raise RuntimeError(result.get("error") or "Ошибка распознавания речи")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError("Распознавание речи превысило допустимое время")


def _delete_remote_transcript(transcript_id: str) -> None:
    response = requests.delete(
        f"{BASE_URL}/v2/transcript/{transcript_id}",
        headers={"authorization": ASSEMBLYAI_API_KEY},
        timeout=30,
    )
    response.raise_for_status()


def _store_web_transcript(text: str) -> None:
    if not text:
        return
    try:
        from flask import has_request_context, session
    except ImportError:
        return
    if not has_request_context():
        return
    user_id = session.get("user_id")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        return
    from core.ai_memory_store import record_ai_memory_event
    record_ai_memory_event(
        user_id,
        "voice_transcript",
        0,
        "recognized",
        {"text": text, "source": "web_voice"},
    )


def _read_audio_bytes(source: str | os.PathLike[str] | BinaryIO) -> bytes:
    if isinstance(source, (str, os.PathLike, Path)):
        with open(source, "rb") as audio:
            return audio.read()
    try:
        source.seek(0)
    except (AttributeError, OSError):
        pass
    data = source.read()
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("Audio source must return bytes")
    return bytes(data)


def _provider_order() -> list[str]:
    raw = os.environ.get("SPEECH_PROVIDER_ORDER", "assemblyai,yandex")
    result = []
    for item in raw.split(","):
        name = item.strip().lower()
        if name in {"assemblyai", "yandex"} and name not in result:
            result.append(name)
    return result or ["assemblyai", "yandex"]


def speech_status() -> dict:
    return {
        "providers": {
            "assemblyai": bool(ASSEMBLYAI_API_KEY),
            "yandex": yandex_configured(),
        },
        "order": _provider_order(),
    }


def _transcribe_assembly(data: bytes) -> str:
    if not ASSEMBLYAI_API_KEY:
        raise RuntimeError("AssemblyAI is not configured")
    audio_url = _upload_audio(BytesIO(data))
    transcript_id = _start_transcription(audio_url)
    try:
        return _wait_for_transcript(transcript_id)
    finally:
        try:
            _delete_remote_transcript(transcript_id)
        except requests.RequestException:
            logger.exception("Failed to delete AssemblyAI transcript %s", transcript_id)


def transcribe_audio(source: str | os.PathLike[str] | BinaryIO) -> str:
    """Transcribe once, falling back between configured providers without retaining raw audio."""
    data = _read_audio_bytes(source)
    if not data:
        return ""
    errors: list[str] = []
    attempted = 0
    for provider in _provider_order():
        if provider == "assemblyai":
            if not ASSEMBLYAI_API_KEY:
                continue
            attempted += 1
            try:
                text = _transcribe_assembly(data)
            except Exception as exc:
                logger.warning("AssemblyAI speech recognition failed: %s", type(exc).__name__)
                errors.append(f"assemblyai:{type(exc).__name__}")
                continue
        elif provider == "yandex":
            if not yandex_configured():
                continue
            attempted += 1
            try:
                text = recognize_oggopus(data)
            except YandexSpeechError as exc:
                logger.warning("Yandex SpeechKit fallback unavailable: %s", exc)
                errors.append(f"yandex:{type(exc).__name__}")
                continue
        else:
            continue
        if text:
            _store_web_transcript(text)
            return text
        errors.append(f"{provider}:empty")
    if attempted == 0:
        raise RuntimeError("No speech recognition provider is configured")
    raise RuntimeError("Speech recognition failed for configured providers: " + ", ".join(errors))
