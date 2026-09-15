"""Yandex SpeechKit short-audio fallback for Russian OggOpus recordings."""

from __future__ import annotations

import os

import requests

YANDEX_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
MAX_SYNC_BYTES = 1024 * 1024


class YandexSpeechError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("YANDEX_SPEECHKIT_API_KEY") or os.environ.get("YANDEX_SPEECHKIT_IAM_TOKEN"))


def _authorization() -> str:
    api_key = str(os.environ.get("YANDEX_SPEECHKIT_API_KEY") or "").strip()
    if api_key:
        return f"Api-Key {api_key}"
    iam = str(os.environ.get("YANDEX_SPEECHKIT_IAM_TOKEN") or "").strip()
    if iam:
        return f"Bearer {iam}"
    raise YandexSpeechError("Yandex SpeechKit credentials are not configured")


def recognize_oggopus(data: bytes) -> str:
    if not configured():
        raise YandexSpeechError("Yandex SpeechKit is not configured")
    if not data or len(data) > MAX_SYNC_BYTES:
        raise YandexSpeechError("Yandex SpeechKit sync fallback accepts audio up to 1 MB")
    if not data.startswith(b"OggS"):
        raise YandexSpeechError("Yandex SpeechKit sync fallback requires OggOpus audio")
    params = {"lang": "ru-RU", "topic": "general", "format": "oggopus"}
    folder_id = str(os.environ.get("YANDEX_SPEECHKIT_FOLDER_ID") or "").strip()
    # folderId is required for user/federated IAM auth and must be omitted for service-account API keys.
    if folder_id and not os.environ.get("YANDEX_SPEECHKIT_API_KEY"):
        params["folderId"] = folder_id
    try:
        response = requests.post(
            YANDEX_STT_URL,
            params=params,
            headers={"Authorization": _authorization()},
            data=data,
            timeout=(5, 35),
        )
    except requests.RequestException as exc:
        raise YandexSpeechError("Yandex SpeechKit request failed") from exc
    if response.status_code != 200:
        raise YandexSpeechError(f"Yandex SpeechKit returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise YandexSpeechError("Yandex SpeechKit returned invalid JSON") from exc
    if payload.get("error_code") is not None:
        raise YandexSpeechError(str(payload.get("error_message") or "Yandex SpeechKit recognition failed"))
    return str(payload.get("result") or "").strip()
