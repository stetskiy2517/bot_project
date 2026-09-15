"""Owner-only aggregate metrics and integration health."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, send_from_directory, session

from core.admin_metrics import product_metrics, system_health
from core.admin_store import is_admin

admin_metrics_api = Blueprint("admin_metrics", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _admin_user_id() -> int:
    value = session.get("user_id")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or not is_admin(value):
        raise PermissionError("admin access required")
    return value


@admin_metrics_api.get("/admin-metrics.js")
def admin_metrics_js():
    _admin_user_id()
    return send_from_directory(WEB_DIR, "admin-metrics.js", mimetype="application/javascript")


@admin_metrics_api.get("/api/admin/metrics")
def metrics():
    _admin_user_id()
    return {"metrics": product_metrics(), "health": system_health()}


@admin_metrics_api.errorhandler(PermissionError)
def forbidden(_error):
    return jsonify(error="forbidden"), 403
