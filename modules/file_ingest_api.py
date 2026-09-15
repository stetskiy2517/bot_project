"""Web API for ephemeral file analysis and confirmed calendar import."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, jsonify, request, send_from_directory, session

from core.db import get_category_colors, get_user_timezone
from core.feature_access import has_ai_access
from integrations.ai import AIConfigurationError, AIProviderError
from modules.calendar import _create_event
from modules.file_ingest import ALLOWED_CATEGORIES, analyze_file_bytes

file_ingest_api = Blueprint("file_ingest", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
FILE_TYPES = {
    ".pdf": ("application/pdf", {"application/pdf"}),
    ".txt": ("text/plain", {"text/plain"}),
    ".doc": ("application/msword", {"application/msword"}),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ),
    ".epub": ("application/epub", {"application/epub", "application/epub+zip"}),
    ".ppt": ("application/ppt", {"application/ppt", "application/vnd.ms-powerpoint"}),
    ".pptx": (
        "application/pptx",
        {"application/pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    ),
    ".xlsx": (
        "application/vnd.ms-excel",
        {"application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ),
    ".jpg": ("image/jpeg", {"image/jpeg"}),
    ".jpeg": ("image/jpeg", {"image/jpeg"}),
    ".png": ("image/png", {"image/png"}),
    ".tif": ("image/tiff", {"image/tiff"}),
    ".tiff": ("image/tiff", {"image/tiff"}),
    ".bmp": ("image/bmp", {"image/bmp"}),
}


def _user() -> int:
    return int(session["user_id"])


def _file_type(upload) -> tuple[str, str, int]:
    raw_name = Path(str(upload.filename or "")).name
    suffix = Path(raw_name).suffix.lower()
    config = FILE_TYPES.get(suffix)
    if config is None:
        raise ValueError("Поддерживаются PDF, DOC/DOCX, PPT/PPTX, XLSX, EPUB, TXT и изображения JPG/PNG/TIFF/BMP")
    provider_mimetype, accepted_mimetypes = config
    supplied = str(upload.mimetype or "").lower().strip()
    if supplied and supplied != "application/octet-stream" and supplied not in accepted_mimetypes:
        raise ValueError("Тип файла не соответствует расширению")
    limit = MAX_IMAGE_BYTES if provider_mimetype.startswith("image/") else MAX_DOCUMENT_BYTES
    provider_name = f"document{suffix}"
    return provider_name, provider_mimetype, limit


def _aware_datetime(value: object, field: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"Не указано {field}")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Некорректное {field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field.capitalize()} должно содержать часовой пояс")
    return parsed


def _valid_timezone(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Некорректный часовой пояс события") from exc
    return raw


@file_ingest_api.get("/file-ingest.js")
def file_ingest_js():
    return send_from_directory(WEB_DIR, "file-ingest.js", mimetype="application/javascript")


@file_ingest_api.post("/api/files/analyze")
def analyze_uploaded_file():
    user_id = _user()
    if not has_ai_access(user_id):
        return jsonify(
            error="ai_access_required",
            message="Разбор файлов требует доступа к ИИ.",
        ), 403
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise ValueError("Выбери файл для загрузки")
    provider_name, mimetype, limit = _file_type(upload)
    content = upload.stream.read(limit + 1)
    if not content:
        raise ValueError("Файл пустой")
    if len(content) > limit:
        size_mb = limit // (1024 * 1024)
        raise ValueError(f"Файл слишком большой. Максимум {size_mb} МБ")
    timezone_name = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    result = analyze_file_bytes(
        content,
        filename=provider_name,
        mimetype=mimetype,
        user_timezone=timezone_name,
    )
    result["ok"] = True
    result["file_kind"] = "image" if mimetype.startswith("image/") else "document"
    return result


@file_ingest_api.post("/api/files/calendar")
def create_event_from_file():
    payload = request.get_json(silent=True) or {}
    proposal = payload.get("event")
    if not isinstance(proposal, dict):
        raise ValueError("Нет события для добавления")
    if proposal.get("ready") is False:
        raise ValueError("Событие требует ручной проверки перед добавлением")

    title = " ".join(str(proposal.get("title") or "").split()).strip()[:200]
    if not title:
        raise ValueError("У события нет названия")
    start = _aware_datetime(proposal.get("start"), "время начала")
    end = _aware_datetime(proposal.get("end"), "время окончания")
    if end <= start:
        raise ValueError("Окончание события должно быть позже начала")
    if end - start > timedelta(days=45):
        raise ValueError("Слишком большая длительность события")
    if start.astimezone(timezone.utc) < datetime.now(timezone.utc) - timedelta(minutes=5):
        raise ValueError("Нельзя добавить событие из прошлого")

    start_timezone = _valid_timezone(proposal.get("start_timezone"))
    end_timezone = _valid_timezone(proposal.get("end_timezone"))
    category = str(proposal.get("category") or "personal").strip().lower()
    if category not in ALLOWED_CATEGORIES:
        category = "personal"
    location = " ".join(str(proposal.get("location") or "").split()).strip()[:500]
    details = str(proposal.get("description") or "").strip()[:1500]
    description = (
        f"AI Smart Planner category: {category}\n"
        "Создано из загруженного файла после подтверждения пользователя."
    )
    if details:
        description += f"\n\n{details}"

    event = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "transparency": "opaque",
        "extendedProperties": {
            "private": {
                "smartPlannerType": "file_import",
                "smartPlannerManaged": "1",
            }
        },
    }
    if start_timezone:
        event["start"]["timeZone"] = start_timezone
    if end_timezone:
        event["end"]["timeZone"] = end_timezone
    if location:
        event["location"] = location
    color = get_category_colors(_user()).get(category)
    if color:
        event["colorId"] = color

    try:
        created = _create_event(_user(), event)
    except PermissionError:
        return jsonify(
            error="calendar_not_connected",
            message="Сначала подключи календарь.",
        ), 409
    return {
        "ok": True,
        "event": {
            "id": created.get("id"),
            "title": created.get("summary") or title,
            "start": (created.get("start") or {}).get("dateTime") or start.isoformat(),
            "end": (created.get("end") or {}).get("dateTime") or end.isoformat(),
            "html_link": created.get("htmlLink"),
        },
    }


@file_ingest_api.errorhandler(ValueError)
def invalid_file_request(error):
    return jsonify(error="invalid_file_request", message=str(error)), 400


@file_ingest_api.errorhandler(AIConfigurationError)
def ai_not_configured(error):
    return jsonify(error="ai_not_configured", message="ИИ для разбора файлов сейчас не настроен."), 503


@file_ingest_api.errorhandler(AIProviderError)
def ai_provider_failed(error):
    return jsonify(error="file_analysis_failed", message="Не удалось разобрать файл через ИИ. Попробуй ещё раз."), 502
