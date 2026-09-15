"""Ephemeral file transport for GigaChat analysis.

Files are kept only in memory locally, uploaded to GigaChat for one analysis request,
and deleted from the provider immediately afterwards by the caller.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

from integrations.ai import (
    AIConfigurationError,
    AIProviderError,
    _ensure_gigachat_ca_bundle,
    _get_access_token,
    _invalidate_token,
    load_ai_settings,
)

DEFAULT_FILE_MODEL = "GigaChat-2-Pro"


def _settings():
    settings = load_ai_settings()
    if not settings.enabled or settings.provider != "gigachat" or not settings.credentials:
        raise AIConfigurationError("AI is not configured")
    return settings


def _post_with_token(settings, url: str, *, verify, **kwargs):
    token = _get_access_token(settings, verify)
    for attempt in range(2):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.post(
                url,
                headers=headers,
                timeout=(5, settings.timeout_seconds),
                verify=verify,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise AIProviderError("GigaChat file request failed") from exc
        if response.status_code == 401 and attempt == 0:
            _invalidate_token(settings)
            token = _get_access_token(settings, verify)
            kwargs["headers"] = headers
            kwargs["headers"].pop("Authorization", None)
            continue
        return response
    raise AIProviderError("GigaChat authorization could not be refreshed")


def upload_file_bytes(content: bytes, filename: str, mimetype: str) -> str:
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise ValueError("File content is empty")
    settings = _settings()
    verify = _ensure_gigachat_ca_bundle(settings)
    safe_name = Path(str(filename or "document")).name[:180] or "document"
    response = _post_with_token(
        settings,
        f"{settings.base_url}/files",
        verify=verify,
        headers={"Accept": "application/json"},
        files={"file": (safe_name, bytes(content), mimetype or "application/octet-stream")},
        data={"purpose": "general"},
    )
    if response.status_code != 200:
        raise AIProviderError(f"GigaChat file upload failed: HTTP {response.status_code}")
    try:
        file_id = str(response.json().get("id") or "").strip()
    except ValueError as exc:
        raise AIProviderError("GigaChat file upload returned invalid JSON") from exc
    if not file_id:
        raise AIProviderError("GigaChat file upload returned no file id")
    return file_id


def complete_with_file(
    file_id: str,
    prompt: str,
    *,
    max_tokens: int = 1600,
    model: str | None = None,
) -> str:
    clean_file_id = str(file_id or "").strip()
    clean_prompt = str(prompt or "").strip()
    if not clean_file_id or not clean_prompt:
        raise ValueError("File id and prompt are required")
    settings = _settings()
    verify = _ensure_gigachat_ca_bundle(settings)
    selected_model = (model or os.environ.get("GIGACHAT_FILE_MODEL") or DEFAULT_FILE_MODEL).strip()
    payload = {
        "model": selected_model,
        "messages": [
            {
                "role": "user",
                "content": clean_prompt[:20000],
                "attachments": [clean_file_id],
            }
        ],
        "function_call": "auto",
        "stream": False,
        "temperature": 0.001,
        "max_tokens": max(128, min(int(max_tokens), 4096)),
    }
    response = _post_with_token(
        settings,
        f"{settings.base_url}/chat/completions",
        verify=verify,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json=payload,
    )
    if response.status_code != 200:
        raise AIProviderError(f"GigaChat file analysis failed: HTTP {response.status_code}")
    try:
        answer = str(response.json()["choices"][0]["message"]["content"] or "").strip()
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("GigaChat file analysis returned an unexpected response") from exc
    if not answer:
        raise AIProviderError("GigaChat file analysis returned an empty response")
    return answer


def delete_file(file_id: str) -> None:
    clean_file_id = str(file_id or "").strip()
    if not clean_file_id:
        return
    settings = _settings()
    verify = _ensure_gigachat_ca_bundle(settings)
    response = _post_with_token(
        settings,
        f"{settings.base_url}/files/{clean_file_id}/delete",
        verify=verify,
        headers={"Accept": "application/json"},
    )
    if response.status_code not in {200, 404}:
        raise AIProviderError(f"GigaChat file delete failed: HTTP {response.status_code}")
