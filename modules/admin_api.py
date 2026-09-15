"""Owner-only administration panel and API."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_from_directory, session

from core.admin_store import (
    admin_overview,
    get_user_for_admin,
    is_admin,
    list_admin_audit,
    list_users_for_admin,
    record_admin_audit,
)
from core.db import conn, db_lock, get_google_account
from core.feature_access import feature_catalog, set_feature_entitlement
from core.web_security import csrf_token

admin_api = Blueprint("admin", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
logger = logging.getLogger(__name__)


def _session_user_id() -> int | None:
    value = session.get("user_id")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _admin_user_id() -> int:
    user_id = _session_user_id()
    if user_id is None or not is_admin(user_id):
        raise PermissionError("admin access required")
    return user_id


@admin_api.before_request
def protect_admin_routes():
    user_id = _session_user_id()
    if user_id is None or not get_google_account(user_id):
        if request.path.startswith("/api/"):
            return jsonify(error="unauthorized"), 401
        return Response("Нужно войти в приложение через Google.", status=401, mimetype="text/plain")
    if not is_admin(user_id):
        if request.path.startswith("/api/"):
            return jsonify(error="forbidden"), 403
        return Response("Доступ запрещён.", status=403, mimetype="text/plain")
    return None


@admin_api.get("/admin")
def admin_page():
    return send_from_directory(WEB_DIR, "admin.html", mimetype="text/html")


@admin_api.get("/admin.js")
def admin_javascript():
    response = send_from_directory(WEB_DIR, "admin.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


@admin_api.get("/api/admin/overview")
def overview():
    _admin_user_id()
    return {
        "overview": admin_overview(),
        "features": feature_catalog(),
        "csrf_token": csrf_token(),
    }


@admin_api.get("/api/admin/users")
def users():
    _admin_user_id()
    query = request.args.get("q", "")
    try:
        limit = int(request.args.get("limit", "200"))
    except (TypeError, ValueError):
        return jsonify(error="invalid_limit"), 400
    return {"users": list_users_for_admin(query, limit=limit)}


@admin_api.get("/api/admin/users/<int:user_id>")
def user_details(user_id: int):
    _admin_user_id()
    user = get_user_for_admin(user_id)
    if not user:
        return jsonify(error="user_not_found"), 404
    return {"user": user, "features": feature_catalog()}


@admin_api.patch("/api/admin/users/<int:user_id>/features/<feature>")
def update_user_feature(user_id: int, feature: str):
    admin_user_id = _admin_user_id()
    if not get_user_for_admin(user_id):
        return jsonify(error="user_not_found"), 404
    payload = request.get_json(silent=True) or {}
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify(error="invalid_feature_state", message="enabled должен быть true или false"), 400
    expires_at = payload.get("expires_at")
    if expires_at is not None and not isinstance(expires_at, str):
        return jsonify(error="invalid_feature_expiry"), 400

    try:
        with db_lock:
            conn.execute("BEGIN IMMEDIATE")
            set_feature_entitlement(
                user_id,
                feature,
                enabled,
                source=f"admin:{admin_user_id}",
                expires_at=expires_at,
                commit=False,
            )
            record_admin_audit(
                admin_user_id,
                "feature_access_changed",
                target_user_id=user_id,
                details={"feature": feature, "enabled": enabled, "expires_at": expires_at},
                commit=False,
            )
            conn.commit()
    except ValueError as exc:
        with db_lock:
            conn.rollback()
        return jsonify(error="invalid_feature", message=str(exc)), 400
    except Exception:
        with db_lock:
            conn.rollback()
        logger.exception("Failed to change feature %s for user %s", feature, user_id)
        return jsonify(error="admin_update_failed", message="Не удалось изменить доступ."), 503

    return {"ok": True, "user": get_user_for_admin(user_id)}


@admin_api.get("/api/admin/audit")
def audit_log():
    _admin_user_id()
    try:
        limit = int(request.args.get("limit", "100"))
    except (TypeError, ValueError):
        return jsonify(error="invalid_limit"), 400
    return {"events": list_admin_audit(limit=limit)}
