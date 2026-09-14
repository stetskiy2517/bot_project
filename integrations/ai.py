"""Provider-neutral AI client with GigaChat support."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import ssl
import threading
import time
import uuid
import warnings

import certifi
import requests
from dotenv import dotenv_values
from urllib3.exceptions import InsecureRequestWarning

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_GIGACHAT_BASE_URL = "https://api.giga.chat/v1"
DEFAULT_GIGACHAT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
DEFAULT_GIGACHAT_CA_URL = "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt"
DEFAULT_GIGACHAT_CA_BUNDLE = PROJECT_ROOT / "data" / "certs" / "gigachat-ca-bundle.pem"
DEFAULT_GIGACHAT_ROOT_CERT = PROJECT_ROOT / "data" / "certs" / "russian_trusted_root_ca_pem.crt"


class AIError(RuntimeError):
    pass


class AIConfigurationError(AIError):
    pass


class AIProviderError(AIError):
    pass


@dataclass(frozen=True)
class AISettings:
    enabled: bool
    provider: str
    model: str
    credentials: str | None
    scope: str
    base_url: str
    auth_url: str
    timeout_seconds: int
    max_output_tokens: int
    ca_bundle: str | None

    @property
    def configured(self) -> bool:
        return self.enabled and self.provider == "gigachat" and bool(self.credentials)


@dataclass
class _TokenEntry:
    access_token: str
    expires_at: float


_token_lock = threading.Lock()
_ca_lock = threading.Lock()
_token_cache: dict[str, _TokenEntry] = {}


def _as_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _bounded_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value not in {None, ""} else default
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def load_ai_settings() -> AISettings:
    file_values = dotenv_values(DOTENV_PATH) if DOTENV_PATH.exists() else {}

    def value(name: str, default: str | None = None) -> str | None:
        process_value = os.environ.get(name)
        if process_value not in {None, ""}:
            return process_value
        file_value = file_values.get(name)
        if file_value not in {None, ""}:
            return str(file_value)
        return default

    credentials = value("GIGACHAT_CREDENTIALS") or value("GIGACHAT_AUTH_KEY")
    configured_bundle = value("GIGACHAT_CA_BUNDLE")
    if not configured_bundle and DEFAULT_GIGACHAT_CA_BUNDLE.is_file():
        configured_bundle = str(DEFAULT_GIGACHAT_CA_BUNDLE)

    return AISettings(
        enabled=_as_bool(value("AI_ENABLED", "1")),
        provider=(value("AI_PROVIDER", "gigachat") or "gigachat").strip().lower(),
        model=(value("GIGACHAT_MODEL", "GigaChat-2") or "GigaChat-2").strip(),
        credentials=credentials.strip() if credentials else None,
        scope=(value("GIGACHAT_SCOPE", "GIGACHAT_API_PERS") or "GIGACHAT_API_PERS").strip(),
        base_url=(value("GIGACHAT_BASE_URL", DEFAULT_GIGACHAT_BASE_URL) or DEFAULT_GIGACHAT_BASE_URL).rstrip("/"),
        auth_url=value("GIGACHAT_AUTH_URL", DEFAULT_GIGACHAT_AUTH_URL) or DEFAULT_GIGACHAT_AUTH_URL,
        timeout_seconds=_bounded_int(value("AI_TIMEOUT_SECONDS"), 30, 5, 120),
        max_output_tokens=_bounded_int(value("AI_MAX_OUTPUT_TOKENS"), 700, 64, 4096),
        ca_bundle=configured_bundle,
    )


def get_ai_status() -> dict:
    settings = load_ai_settings()
    return {
        "enabled": settings.enabled,
        "provider": settings.provider,
        "configured": settings.configured,
        "model": settings.model,
    }


def is_ai_available() -> bool:
    return load_ai_settings().configured


def _decode_certificate(path: Path) -> dict:
    try:
        return ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
    except Exception as exc:
        raise AIProviderError("Downloaded GigaChat CA certificate is invalid") from exc


def _common_name(parts) -> str:
    for group in parts or ():
        for key, value in group:
            if key == "commonName":
                return str(value)
    return ""


def _validate_gigachat_root(path: Path) -> None:
    decoded = _decode_certificate(path)
    subject = _common_name(decoded.get("subject"))
    issuer = _common_name(decoded.get("issuer"))
    if subject != "Russian Trusted Root CA" or issuer != "Russian Trusted Root CA":
        raise AIProviderError("Unexpected certificate returned for GigaChat CA")
    not_after = decoded.get("notAfter")
    if not not_after:
        raise AIProviderError("GigaChat CA certificate has no expiration date")
    try:
        expires_at = ssl.cert_time_to_seconds(not_after)
    except (TypeError, ValueError) as exc:
        raise AIProviderError("GigaChat CA certificate expiration is invalid") from exc
    if expires_at <= time.time():
        raise AIProviderError("GigaChat CA certificate has expired")


def _ensure_gigachat_ca_bundle(settings: AISettings) -> str | bool:
    if settings.ca_bundle:
        bundle = Path(settings.ca_bundle).expanduser()
        if not bundle.is_file():
            raise AIConfigurationError("GIGACHAT_CA_BUNDLE points to a missing file")
        return str(bundle)

    with _ca_lock:
        DEFAULT_GIGACHAT_CA_BUNDLE.parent.mkdir(parents=True, exist_ok=True)
        if DEFAULT_GIGACHAT_CA_BUNDLE.is_file() and DEFAULT_GIGACHAT_ROOT_CERT.is_file():
            try:
                _validate_gigachat_root(DEFAULT_GIGACHAT_ROOT_CERT)
                return str(DEFAULT_GIGACHAT_CA_BUNDLE)
            except AIProviderError:
                logger.warning("Stored GigaChat CA bundle is invalid; rebuilding it")
                DEFAULT_GIGACHAT_ROOT_CERT.unlink(missing_ok=True)
                DEFAULT_GIGACHAT_CA_BUNDLE.unlink(missing_ok=True)

        root_tmp = DEFAULT_GIGACHAT_ROOT_CERT.with_suffix(".tmp")
        bundle_tmp = DEFAULT_GIGACHAT_CA_BUNDLE.with_suffix(".tmp")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", InsecureRequestWarning)
                response = requests.get(
                    DEFAULT_GIGACHAT_CA_URL,
                    timeout=(5, 20),
                    verify=False,
                )
            if response.status_code != 200:
                raise AIProviderError(
                    f"Could not download GigaChat CA certificate: HTTP {response.status_code}"
                )
            content = bytes(response.content or b"")
            if not 500 <= len(content) <= 65536:
                raise AIProviderError("GigaChat CA certificate has an unexpected size")
            if b"-----BEGIN CERTIFICATE-----" not in content or b"-----END CERTIFICATE-----" not in content:
                raise AIProviderError("GigaChat CA certificate is not PEM encoded")

            root_tmp.write_bytes(content)
            _validate_gigachat_root(root_tmp)
            shutil.copyfile(certifi.where(), bundle_tmp)
            with bundle_tmp.open("ab") as target:
                target.write(b"\n")
                target.write(content)
                target.write(b"\n")
            root_tmp.replace(DEFAULT_GIGACHAT_ROOT_CERT)
            bundle_tmp.replace(DEFAULT_GIGACHAT_CA_BUNDLE)
            logger.info("Prepared isolated GigaChat CA bundle")
            return str(DEFAULT_GIGACHAT_CA_BUNDLE)
        finally:
            root_tmp.unlink(missing_ok=True)
            bundle_tmp.unlink(missing_ok=True)


def _cache_key(settings: AISettings) -> str:
    if not settings.credentials:
        return "missing"
    digest = hashlib.sha256(settings.credentials.encode("utf-8")).hexdigest()
    return f"{settings.scope}:{digest}"


def _token_expiration(value) -> float:
    try:
        expires_at = float(value)
    except (TypeError, ValueError):
        return time.time() + 25 * 60
    if expires_at > 10**11:
        expires_at /= 1000.0
    return expires_at


def _invalidate_token(settings: AISettings) -> None:
    with _token_lock:
        _token_cache.pop(_cache_key(settings), None)


def _get_access_token(settings: AISettings, verify: str | bool) -> str:
    if not settings.credentials:
        raise AIConfigurationError("GigaChat credentials are not configured")
    key = _cache_key(settings)
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached.expires_at - 60 > time.time():
            return cached.access_token

        credentials = settings.credentials.strip()
        if credentials.lower().startswith("basic "):
            credentials = credentials[6:].strip()
        try:
            response = requests.post(
                settings.auth_url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Basic {credentials}",
                    "RqUID": str(uuid.uuid4()),
                },
                data={"scope": settings.scope},
                timeout=(5, settings.timeout_seconds),
                verify=verify,
            )
        except requests.RequestException as exc:
            raise AIProviderError("GigaChat authorization request failed") from exc
        if response.status_code != 200:
            raise AIProviderError(f"GigaChat authorization failed: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AIProviderError("GigaChat authorization returned invalid JSON") from exc
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise AIProviderError("GigaChat authorization returned no access token")
        expires_at = _token_expiration(payload.get("expires_at"))
        _token_cache[key] = _TokenEntry(access_token=access_token, expires_at=expires_at)
        return access_token


def _validate_messages(messages: list[dict]) -> list[dict]:
    if not isinstance(messages, list) or not messages:
        raise ValueError("AI messages must be a non-empty list")
    result = []
    for item in messages:
        if not isinstance(item, dict):
            raise ValueError("Each AI message must be an object")
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in {"system", "user", "assistant"} or not content:
            raise ValueError("AI message must contain a supported role and text")
        result.append({"role": role, "content": content[:20000]})
    return result


def _gigachat_completion(
    settings: AISettings,
    messages: list[dict],
    *,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> str:
    verify = _ensure_gigachat_ca_bundle(settings)
    clean_messages = _validate_messages(messages)
    token = _get_access_token(settings, verify)
    payload = {
        "model": settings.model,
        "messages": clean_messages,
        "stream": False,
        "temperature": max(0.001, min(2.0, float(temperature))),
        "max_tokens": max_tokens or settings.max_output_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    for attempt in range(2):
        try:
            response = requests.post(
                f"{settings.base_url}/chat/completions",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=(5, settings.timeout_seconds),
                verify=verify,
            )
        except requests.RequestException as exc:
            raise AIProviderError("GigaChat completion request failed") from exc
        if response.status_code == 401 and attempt == 0:
            _invalidate_token(settings)
            token = _get_access_token(settings, verify)
            continue
        if response.status_code != 200:
            raise AIProviderError(f"GigaChat completion failed: HTTP {response.status_code}")
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("GigaChat completion returned an unexpected response") from exc
        answer = str(content or "").strip()
        if not answer:
            raise AIProviderError("GigaChat completion returned an empty response")
        return answer
    raise AIProviderError("GigaChat authorization could not be refreshed")


def complete(
    messages: list[dict],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> str:
    settings = load_ai_settings()
    if not settings.enabled:
        raise AIConfigurationError("AI is disabled")
    if settings.provider != "gigachat":
        raise AIConfigurationError(f"Unsupported AI provider: {settings.provider}")
    if not settings.credentials:
        raise AIConfigurationError("GigaChat credentials are not configured")
    return _gigachat_completion(
        settings,
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def complete_structured(
    messages: list[dict],
    schema: dict,
    *,
    max_tokens: int | None = None,
) -> dict:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("Structured AI schema must be a JSON object schema")
    settings = load_ai_settings()
    if not settings.enabled or settings.provider != "gigachat" or not settings.credentials:
        raise AIConfigurationError("AI is not configured")
    if settings.scope == "GIGACHAT_API_PERS":
        raise AIProviderError("GigaChat structured output unavailable for personal scope: HTTP 400")
    raw = _gigachat_completion(
        settings,
        messages,
        response_format={"type": "json_schema", "schema": schema, "strict": True},
        max_tokens=max_tokens,
        temperature=0.001,
    )
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIProviderError("GigaChat structured response is not valid JSON") from exc
    if not isinstance(result, dict):
        raise AIProviderError("GigaChat structured response is not an object")
    return result


def _reset_token_cache_for_tests() -> None:
    with _token_lock:
        _token_cache.clear()
