"""User-owned subjective ratings for the life-balance view."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, request, send_from_directory, session

from core.life_balance_store import get_life_balance_ratings, save_life_balance_rating

life_balance_api = Blueprint("life_balance", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _user() -> int:
    return int(session["user_id"])


@life_balance_api.after_app_request
def life_balance_ui_hook(response):
    if request.path == "/" and response.status_code == 200 and response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        script = '<script src="/life-balance.js"></script>'
        if script not in html and "</body>" in html:
            response.set_data(html.replace("</body>", f"    {script}\n  </body>", 1))
    return response


@life_balance_api.get("/life-balance.js")
def life_balance_js():
    return send_from_directory(WEB_DIR, "life-balance.js", mimetype="application/javascript")


@life_balance_api.get("/api/life-balance/ratings")
def ratings():
    return {"ratings": get_life_balance_ratings(_user())}


@life_balance_api.put("/api/life-balance/ratings/<category>")
def update_rating(category: str):
    payload = request.get_json(silent=True) or {}
    if set(payload) - {"rating", "target"}:
        raise ValueError("Неизвестные поля оценки")
    return {
        "rating": save_life_balance_rating(
            _user(), category, rating=payload.get("rating"), target=payload.get("target")
        )
    }
