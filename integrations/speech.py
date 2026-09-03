"""Speech-to-text integration shared by web and Telegram transports."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import BinaryIO

import requests

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
BASE_URL = "https://api.assemblyai.com"
TRANSCRIPTION_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2


def normalize_time_format(text: str) -> str:
    """Normalize speech recognition time such as 22.00 -> 22:00."""
    return re.sub(r"\b([01]?\d|2[0-3])\.(\d{2})\b", r"\1:\2", text)


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
            return result.get("text", "").strip()
        if status == "error":
            raise RuntimeError(result.get("error") or "Ошибка распознавания речи")
        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError("Распознавание речи превысило допустимое время")


def transcribe_audio(source: str | os.PathLike[str] | BinaryIO) -> str:
    """Transcribe a local audio path or an already opened binary stream."""
    if not ASSEMBLYAI_API_KEY:
        raise RuntimeError("ASSEMBLYAI_API_KEY not set")

    if isinstance(source, (str, os.PathLike, Path)):
        with open(source, "rb") as audio:
            audio_url = _upload_audio(audio)
    else:
        audio_url = _upload_audio(source)

    transcript_id = _start_transcription(audio_url)
    return _wait_for_transcript(transcript_id)
