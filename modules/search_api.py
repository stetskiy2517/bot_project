"""Local cross-module search with explicit selection into shared context."""
from pathlib import Path
from flask import Blueprint, jsonify, request, session, send_from_directory
from core.search_api_store import SPECS, memory_entity, search_local, result_item
from core.memory_store import search_work_memory
from core.conversation_context import remember_entity_for_user

search_api = Blueprint("search", __name__)


@search_api.get("/search.js")
def script():
    return send_from_directory(Path(__file__).resolve().parents[1] / "web", "search.js", mimetype="application/javascript")


@search_api.after_app_request
def load_search(response):
    if request.path == "/" and response.status_code == 200 and response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        script = '<script defer src="/search.js"></script>'
        if script not in html:
            response.set_data(html.replace("</body>", script+"\n</body>", 1))
    return response


@search_api.get("/api/search")
def search():
    query = (request.args.get("q") or "").strip()
    kind = request.args.get("kind")
    if not 1 <= len(query) <= 300 or (kind and kind not in SPECS):
        return jsonify(error="invalid_search", message="Введите запрос до 300 символов"), 400
    groups = search_local(session["user_id"], query, kind=kind, before=request.args.get("before", type=int))
    related = []
    if not kind:
        matches = {(item["kind"], item["id"]) for group in groups.values() for item in group["items"]}
        for plural, items in search_work_memory(session["user_id"], query, limit=20).items():
            name = {"companies":"company", "contacts":"contact", "interactions":"interaction", "commitments":"commitment"}[plural]
            for item in items:
                result = result_item(name, item)
                if (name, result["id"]) not in matches:
                    related.append(result)
    return {"groups": groups, "related": related, "query": query}


@search_api.post("/api/search/open")
def open_result():
    payload = request.get_json()
    kind = payload.get("kind")
    try:
        item_id = int(payload.get("id"))
    except (TypeError, ValueError):
        return jsonify(error="invalid_search_item"), 400
    item = memory_entity(session["user_id"], kind, item_id)
    if not item:
        return jsonify(error="search_item_not_found"), 404
    if kind in {"note", "task"}:
        remember_entity_for_user(session["user_id"], kind, item_id, item.get("title") or "")
    return {"item": item, "kind": kind}
