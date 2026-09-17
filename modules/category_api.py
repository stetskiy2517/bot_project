"""Web API and UI hook for user-owned calendar categories."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory, session

from core.category_store import (
    create_user_category,
    delete_user_category,
    get_user_categories,
    install_dynamic_category_support,
    update_user_category,
)

category_api = Blueprint("category_management", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

install_dynamic_category_support()


def _user() -> int:
    return int(session["user_id"])


def _payload() -> dict:
    value = request.get_json(silent=True) or {}
    if not isinstance(value, dict):
        raise ValueError("Ожидается JSON-объект")
    return value


@category_api.after_app_request
def inject_category_manager(response):
    if request.path != "/" or response.status_code != 200 or response.mimetype != "text/html":
        return response
    html = response.get_data(as_text=True)
    script = '<script src="/category-manager.js"></script>'
    if script not in html and "</body>" in html:
        html = html.replace("</body>", f"    {script}\n  </body>", 1)
        response.set_data(html)
    return response


@category_api.get("/category-manager.js")
def category_manager_js():
    return send_from_directory(WEB_DIR, "category-manager.js", mimetype="application/javascript")


@category_api.get("/api/categories")
def list_categories():
    return {"categories": get_user_categories(_user())}


@category_api.post("/api/categories")
def create_category():
    payload = _payload()
    try:
        category = create_user_category(_user(), payload.get("label"), payload.get("color_id"))
    except ValueError as exc:
        return jsonify(error="invalid_category", message=str(exc)), 400
    return {"category": category, "categories": get_user_categories(_user())}, 201


@category_api.patch("/api/categories/<category_key>")
def update_category(category_key: str):
    payload = _payload()
    kwargs = {}
    if "label" in payload:
        kwargs["label"] = payload.get("label")
    if "color_id" in payload:
        kwargs["color_id"] = payload.get("color_id")
    if not kwargs:
        return jsonify(error="invalid_category", message="Нет изменений для сохранения"), 400
    try:
        category = update_user_category(_user(), category_key, **kwargs)
    except KeyError:
        return jsonify(error="category_not_found", message="Категория не найдена"), 404
    except ValueError as exc:
        return jsonify(error="invalid_category", message=str(exc)), 400
    return {"category": category, "categories": get_user_categories(_user())}


@category_api.delete("/api/categories/<category_key>")
def delete_category(category_key: str):
    try:
        deleted = delete_user_category(_user(), category_key)
    except ValueError as exc:
        return jsonify(error="invalid_category", message=str(exc)), 400
    if not deleted:
        return jsonify(error="category_not_found", message="Категория не найдена"), 404
    return {
        "ok": True,
        "categories": get_user_categories(_user()),
        "events_deleted": False,
    }
