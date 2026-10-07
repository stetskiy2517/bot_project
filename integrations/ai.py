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
import secrets
import threading
import time
import uuid

import certifi
import requests
from dotenv import dotenv_values

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_GIGACHAT_BASE_URL = "https://api.giga.chat/v1"
DEFAULT_GIGACHAT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
DEFAULT_GIGACHAT_CA_URL = "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt"
DEFAULT_GIGACHAT_CA_BUNDLE = PROJECT_ROOT / "data" / "certs" / "gigachat-ca-bundle.pem"
DEFAULT_GIGACHAT_ROOT_CERT = PROJECT_ROOT / "data" / "certs" / "russian_trusted_root_ca_pem.crt"
TRANSIENT_GIGACHAT_STATUSES = frozenset({429, 500, 502, 503, 504})
COMPLETION_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
RETRY_MAX_SECONDS = 4.0


class AIError(RuntimeError):
    pass


class AIConfigurationError(AIError):
    pass


class AIProviderError(AIError):
    pass


class AIRateLimitError(AIProviderError):
    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


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
    root_sha256: str | None = None

    @property
    def configured(self) -> bool:
        return self.enabled and self.provider == "gigachat" and bool(self.credentials)


@dataclass
class _TokenEntry:
    access_token: str
    expires_at: float


_token_lock = threading.Lock()
_ca_lock = threading.Lock()
_personal_completion_lock = threading.Lock()
_provider_state_lock = threading.Lock()
_token_cache: dict[str, _TokenEntry] = {}
_provider_state = {
    "state": "unknown",
    "last_error": None,
    "retry_after_seconds": None,
    "updated_at": None,
    "last_success_at": None,
    "last_error_at": None,
}


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
        root_sha256=value("GIGACHAT_ROOT_SHA256"),
    )


def _set_provider_state(state: str, error: str | None = None, retry_after: float | None = None) -> None:
    with _provider_state_lock:
        now = time.time()
        _provider_state.update(
            state=state,
            last_error=error,
            retry_after_seconds=(round(max(0.0, retry_after), 3) if retry_after is not None else None),
            updated_at=now,
        )
        if state == "healthy":
            _provider_state["last_success_at"] = now
        elif error:
            _provider_state["last_error_at"] = now


def _provider_status_snapshot() -> dict:
    with _provider_state_lock:
        return dict(_provider_state)


def get_ai_status() -> dict:
    settings = load_ai_settings()
    runtime = _provider_status_snapshot()
    if runtime["updated_at"] is not None and time.time() - runtime["updated_at"] > 3600:
        runtime = {**runtime, "state": "unknown", "retry_after_seconds": None}
    if not settings.enabled:
        runtime = {**runtime, "state": "disabled"}
    elif not settings.configured:
        runtime = {**runtime, "state": "unconfigured"}
    return {
        "enabled": settings.enabled,
        "provider": settings.provider,
        "configured": settings.configured,
        "model": settings.model,
        **runtime,
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


def _validate_gigachat_root(path: Path, expected_sha256: str | None = None) -> bytes:
    # The pin must come from independent trusted operator configuration, never
    # from the same HTTP response or an automatically cached certificate.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.serialization import Encoding

    pin = str(expected_sha256 or "").replace(":", "").strip().lower()
    if len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
        raise AIConfigurationError("Set GIGACHAT_ROOT_SHA256 from an independently verified certificate")
    try:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
        actual = cert.fingerprint(hashes.SHA256()).hex()
    except ValueError as exc:
        raise AIProviderError("Invalid GigaChat root certificate") from exc
    if not secrets.compare_digest(actual, pin):
        raise AIProviderError("GigaChat root certificate fingerprint mismatch")
    try:
        if not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise AIProviderError("GigaChat certificate is not a CA")
    except x509.ExtensionNotFound as exc:
        raise AIProviderError("GigaChat certificate has no CA constraint") from exc
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
    try:
        starts_at = ssl.cert_time_to_seconds(decoded.get("notBefore"))
    except (TypeError, ValueError) as exc:
        raise AIProviderError("GigaChat CA certificate start date is invalid") from exc
    if starts_at > time.time():
        raise AIProviderError("GigaChat CA certificate is not valid yet")
    # Trust only the pinned certificate, never additional PEM certificates or
    # trusted-certificate directives supplied alongside it.
    return cert.public_bytes(Encoding.PEM)


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
                verified_root = _validate_gigachat_root(DEFAULT_GIGACHAT_ROOT_CERT, settings.root_sha256)
                # Rebuild the bundle even for a valid cached root: older versions
                # downloaded without TLS verification and the bundle may differ.
                rebuilt = DEFAULT_GIGACHAT_CA_BUNDLE.with_suffix(".tmp")
                with rebuilt.open("wb") as target:
                    target.write(Path(certifi.where()).read_bytes())
                    target.write(b"\n" + verified_root + b"\n")
                rebuilt.replace(DEFAULT_GIGACHAT_CA_BUNDLE)
                return str(DEFAULT_GIGACHAT_CA_BUNDLE)
            except AIProviderError:
                logger.warning("Stored GigaChat CA bundle is invalid; rebuilding it")
                DEFAULT_GIGACHAT_ROOT_CERT.unlink(missing_ok=True)
                DEFAULT_GIGACHAT_CA_BUNDLE.unlink(missing_ok=True)

        root_tmp = DEFAULT_GIGACHAT_ROOT_CERT.with_suffix(".tmp")
        bundle_tmp = DEFAULT_GIGACHAT_CA_BUNDLE.with_suffix(".tmp")
        try:
            # Validate the pin before making a request, even on a fresh machine.
            pin = str(settings.root_sha256 or "").replace(":", "").strip().lower()
            if len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
                raise AIConfigurationError("Set GIGACHAT_ROOT_SHA256 or a trusted GIGACHAT_CA_BUNDLE")
            try:
                response = requests.get(DEFAULT_GIGACHAT_CA_URL, timeout=(5, 20), verify=True)
            except requests.RequestException as exc:
                raise AIProviderError("Could not securely download GigaChat root certificate") from exc
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
            content = _validate_gigachat_root(root_tmp, settings.root_sha256)
            root_tmp.write_bytes(content)
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


def _retry_after_seconds(response, attempt: int) -> float:
    headers = getattr(response, "headers", {}) or {}
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    if raw not in {None, ""}:
        try:
            return min(RETRY_MAX_SECONDS, max(0.0, float(raw)))
        except (TypeError, ValueError):
            pass
    return min(RETRY_MAX_SECONDS, RETRY_BACKOFF_SECONDS * (2**attempt))


def _completion_request(
    settings: AISettings,
    messages: list[dict],
    *,
    response_format: dict | None,
    max_tokens: int | None,
    temperature: float,
) -> str:
    try:
        verify = _ensure_gigachat_ca_bundle(settings)
    except AIError:
        _set_provider_state("degraded", "trust_configuration_error")
        raise
    clean_messages = _validate_messages(messages)
    try:
        token = _get_access_token(settings, verify)
    except AIError:
        _set_provider_state("degraded", "authorization_error")
        raise
    payload = {
        "model": settings.model,
        "messages": clean_messages,
        "stream": False,
        "temperature": max(0.001, min(2.0, float(temperature))),
        "max_tokens": max_tokens or settings.max_output_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    refreshed_token = False
    last_retry_after = None
    for attempt in range(COMPLETION_MAX_ATTEMPTS):
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
            if attempt + 1 < COMPLETION_MAX_ATTEMPTS:
                delay = min(RETRY_MAX_SECONDS, RETRY_BACKOFF_SECONDS * (2**attempt))
                _set_provider_state("degraded", "transport_error", delay)
                time.sleep(delay)
                continue
            _set_provider_state("degraded", "transport_error")
            raise AIProviderError("GigaChat completion request failed") from exc

        if response.status_code == 401 and not refreshed_token:
            refreshed_token = True
            _invalidate_token(settings)
            token = _get_access_token(settings, verify)
            continue

        if response.status_code in TRANSIENT_GIGACHAT_STATUSES:
            delay = _retry_after_seconds(response, attempt)
            last_retry_after = delay
            error_kind = "rate_limited" if response.status_code == 429 else f"http_{response.status_code}"
            _set_provider_state("degraded", error_kind, delay)
            if attempt + 1 < COMPLETION_MAX_ATTEMPTS:
                time.sleep(delay)
                continue
            if response.status_code == 429:
                raise AIRateLimitError(
                    "GigaChat rate limit exceeded",
                    retry_after=last_retry_after,
                )
            raise AIProviderError(f"GigaChat completion failed: HTTP {response.status_code}")

        if response.status_code != 200:
            _set_provider_state("degraded", f"http_{response.status_code}")
            raise AIProviderError(f"GigaChat completion failed: HTTP {response.status_code}")
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            _set_provider_state("degraded", "invalid_response")
            raise AIProviderError("GigaChat completion returned an unexpected response") from exc
        answer = str(content or "").strip()
        if not answer:
            _set_provider_state("degraded", "empty_response")
            raise AIProviderError("GigaChat completion returned an empty response")
        _set_provider_state("healthy")
        return answer
    raise AIProviderError("GigaChat completion failed after retries")


def _gigachat_completion(
    settings: AISettings,
    messages: list[dict],
    *,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> str:
    if settings.scope != "GIGACHAT_API_PERS":
        return _completion_request(
            settings,
            messages,
            response_format=response_format,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    wait_timeout = max(1.0, float(settings.timeout_seconds) + 5.0)
    acquired = _personal_completion_lock.acquire(timeout=wait_timeout)
    if not acquired:
        _set_provider_state("degraded", "personal_scope_busy", 1.0)
        raise AIRateLimitError("GigaChat personal request queue is busy", retry_after=1.0)
    try:
        return _completion_request(
            settings,
            messages,
            response_format=response_format,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    finally:
        _personal_completion_lock.release()


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


def _parse_json_object_response(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.replace("```json", "", 1).replace("```JSON", "", 1)
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise AIProviderError("GigaChat structured response does not contain a valid JSON object")


def _structured_fallback_messages(messages: list[dict], schema: dict) -> list[dict]:
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    instruction = (
        "Верни только один JSON-объект без markdown, комментариев и дополнительного текста. "
        "JSON должен соответствовать этой схеме: " + schema_text
    )
    clean = _validate_messages(messages)
    if clean and clean[0]["role"] == "system":
        return [
            {"role": "system", "content": clean[0]["content"] + "\n\n" + instruction},
            *clean[1:],
        ]
    return [{"role": "system", "content": instruction}, *clean]


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
        raw = _gigachat_completion(
            settings,
            _structured_fallback_messages(messages, schema),
            max_tokens=max_tokens,
            temperature=0.001,
        )
        return _parse_json_object_response(raw)

    raw = _gigachat_completion(
        settings,
        messages,
        response_format={"type": "json_schema", "schema": schema, "strict": True},
        max_tokens=max_tokens,
        temperature=0.001,
    )
    return _parse_json_object_response(raw)


def _reset_token_cache_for_tests() -> None:
    with _token_lock:
        _token_cache.clear()
    with _provider_state_lock:
        _provider_state.update(
            state="unknown",
            last_error=None,
            retry_after_seconds=None,
            updated_at=None,
            last_success_at=None,
            last_error_at=None,
        )
