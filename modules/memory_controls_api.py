"""User-facing controls for learned memory and proactive feedback."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory, session

from core.memory_store import (
    create_commitment,
    create_company,
    create_contact,
    list_commitments,
    list_companies,
    list_contacts,
    list_interactions,
    list_memories,
    record_interaction,
    suppress_memory,
    upsert_memory,
)
from core.proactive_feedback_store import feedback_for_actions, feedback_summary, save_proactive_feedback
from core.proactive_store import list_proactive_actions

memory_controls_api = Blueprint("memory_controls", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _user() -> int:
    return int(session["user_id"])


def _find_memory(user_id: int, memory_id: int) -> dict | None:
    for status in ("active", "suppressed"):
        for item in list_memories(user_id, status=status, limit=500):
            if int(item["memory_id"]) == int(memory_id):
                return item
    return None


@memory_controls_api.after_app_request
def memory_ui_hook(response):
    if request.path == "/" and response.status_code == 200 and response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        script = '<script src="/memory-controls.js"></script>'
        if script not in html and "</body>" in html:
            response.set_data(html.replace("</body>", f"    {script}\n  </body>", 1))
    return response


@memory_controls_api.get("/memory-controls.js")
def memory_controls_js():
    return send_from_directory(WEB_DIR, "memory-controls.js", mimetype="application/javascript")


@memory_controls_api.get("/api/memory/controls")
def memory_controls():
    user_id = _user()
    memories = list_memories(user_id, limit=200)
    actions = list_proactive_actions(user_id, limit=100)
    feedback = feedback_for_actions(user_id, [item["action_id"] for item in actions])
    return {
        "memories": memories,
        "actions": [{**item, "feedback": feedback.get(int(item["action_id"]))} for item in actions],
        "feedback_summary": feedback_summary(user_id),
    }


@memory_controls_api.patch("/api/memory/<int:memory_id>")
def correct_memory(memory_id: int):
    user_id = _user()
    current = _find_memory(user_id, memory_id)
    if not current or current.get("status") != "active":
        return jsonify(error="memory_not_found"), 404
    payload = request.get_json(silent=True) or {}
    if set(payload) != {"value"}:
        return jsonify(error="invalid_memory_update", message="Можно изменить только значение памяти."), 400
    value = payload.get("value")
    if value is None or (isinstance(value, str) and not value.strip()):
        return jsonify(error="invalid_memory_value", message="Новое значение не может быть пустым."), 400
    previous_evidence = " ".join(str(current.get("evidence") or "").split()).strip()
    evidence = "Исправлено пользователем"
    if previous_evidence:
        evidence += f". Исходное основание: {previous_evidence[:350]}"
    memory = upsert_memory(
        user_id,
        current["kind"],
        current["key"],
        value,
        1.0,
        source_type="user_correction",
        source_id=memory_id,
        evidence=evidence,
    )
    return {"memory": memory}


@memory_controls_api.delete("/api/memory/<int:memory_id>")
def forget_memory(memory_id: int):
    if not suppress_memory(_user(), memory_id):
        return jsonify(error="memory_not_found"), 404
    return {"ok": True, "memory_id": memory_id}


@memory_controls_api.post("/api/proactive/<int:action_id>/feedback")
def proactive_feedback(action_id: int):
    user_id = _user()
    action = next(
        (item for item in list_proactive_actions(user_id, limit=100) if int(item["action_id"]) == int(action_id)),
        None,
    )
    if not action:
        return jsonify(error="proactive_action_not_found"), 404
    payload = request.get_json(silent=True) or {}
    value = str(payload.get("feedback") or "").strip().lower()
    result = save_proactive_feedback(user_id, action_id, int(action["memory_id"]), value)
    if value == "never":
        suppress_memory(user_id, int(action["memory_id"]))
        result["memory_suppressed"] = True
    return {"feedback": result}


@memory_controls_api.get("/api/memory/work-context")
def memory_work_context():
    user_id = _user()
    return {
        "companies": list_companies(user_id, limit=200),
        "contacts": list_contacts(user_id, limit=300),
        "interactions": list_interactions(user_id, limit=200),
        "commitments": list_commitments(user_id, status="open", limit=200),
    }


@memory_controls_api.post("/api/memory/companies")
def add_memory_company():
    payload = request.get_json(silent=True) or {}
    try:
        item = create_company(
            _user(),
            payload.get("name"),
            industry=payload.get("industry"),
            website=payload.get("website"),
            notes=payload.get("notes"),
        )
    except ValueError as exc:
        return jsonify(error="invalid_company", message=str(exc)), 400
    return {"company": item}, 201


@memory_controls_api.post("/api/memory/contacts")
def add_memory_contact():
    payload = request.get_json(silent=True) or {}
    try:
        item = create_contact(
            _user(),
            payload.get("full_name"),
            company_id=payload.get("company_id"),
            position=payload.get("position"),
            phone=payload.get("phone"),
            email=payload.get("email"),
            telegram=payload.get("telegram"),
            notes=payload.get("notes"),
        )
    except (TypeError, ValueError) as exc:
        return jsonify(error="invalid_contact", message=str(exc)), 400
    return {"contact": item}, 201


@memory_controls_api.post("/api/memory/interactions")
def add_memory_interaction():
    payload = request.get_json(silent=True) or {}
    try:
        item = record_interaction(
            _user(),
            payload.get("interaction_type"),
            payload.get("summary"),
            happened_at=payload.get("happened_at"),
            company_id=payload.get("company_id"),
            contact_id=payload.get("contact_id"),
            outcome=payload.get("outcome"),
            next_step=payload.get("next_step"),
            source_type="user",
            source_id="memory_screen",
        )
    except (TypeError, ValueError) as exc:
        return jsonify(error="invalid_interaction", message=str(exc)), 400
    return {"interaction": item}, 201


@memory_controls_api.post("/api/memory/commitments")
def add_memory_commitment():
    payload = request.get_json(silent=True) or {}
    try:
        item = create_commitment(
            _user(),
            payload.get("title"),
            due_at=payload.get("due_at"),
            company_id=payload.get("company_id"),
            contact_id=payload.get("contact_id"),
            source_type="user",
            source_id="memory_screen",
        )
    except (TypeError, ValueError) as exc:
        return jsonify(error="invalid_commitment", message=str(exc)), 400
    return {"commitment": item}, 201
