"""Web endpoints for approved assistant features; no generic parsed-command card."""
from __future__ import annotations

import asyncio
import json

from flask import Blueprint, Response, g, jsonify, request, session

from core.assistant_store import (
    alert_preferences, assistant_preferences, delete_template, get_template,
    list_templates, save_alert_preferences, save_assistant_preferences, save_template,
)
from core.privacy_store import delete_ticket, export_local_data, privacy_notice, purge_local_account
from core.undo_store import last_undo, undo_action
from core.web_transport import WebContext, WebUpdate
from modules.assistant_commands import daily_overview, prepare_template


def assistant_blueprint(*, state_for, clear_state) -> Blueprint:
    blueprint = Blueprint("assistant", __name__)

    def uid() -> int:
        return session["user_id"]

    def payload() -> dict:
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError("Ожидается JSON-объект.")
        return value

    @blueprint.errorhandler(ValueError)
    def bad_value(error):
        return jsonify({"error": "invalid_input", "message": str(error)}), 400

    @blueprint.errorhandler(LookupError)
    def missing_value(error):
        return jsonify({"error": "not_found", "message": str(error)}), 404

    @blueprint.get("/api/assistant/preferences")
    def preferences():
        return assistant_preferences(uid())

    @blueprint.post("/api/assistant/preferences")
    def update_preferences():
        return save_assistant_preferences(uid(), payload())

    @blueprint.get("/api/assistant/overview")
    def overview():
        return daily_overview(uid(), evening=request.args.get("mode") == "evening")

    @blueprint.get("/api/templates")
    def templates():
        return {"templates": list_templates(uid())}

    @blueprint.post("/api/templates")
    def new_template():
        return {"template": save_template(uid(), payload())}

    @blueprint.patch("/api/templates/<int:template_id>")
    def update_template(template_id):
        return {"template": save_template(uid(), payload(), template_id)}

    @blueprint.delete("/api/templates/<int:template_id>")
    def remove_template(template_id):
        if not delete_template(uid(), template_id):
            raise LookupError("Шаблон не найден.")
        return {"ok": True}

    @blueprint.post("/api/templates/<int:template_id>/use")
    def use_template(template_id):
        template = get_template(uid(), template_id)
        if not template:
            raise LookupError("Шаблон не найден.")
        date_text = payload().get("date", "")
        if not isinstance(date_text, str) or len(date_text) > 200:
            raise ValueError("Некорректная дата.")
        update = WebUpdate(uid(), "", date_text)
        context = WebContext(state_for(uid()))
        asyncio.run(prepare_template(update, context, template, date_text))
        confirmed = (context.user_data.get("smart_planner_pending") or {}).get("type") == "template_confirm"
        return {"handled": True, "replies": update.message.replies,
                "actions": [{"label": "Создать", "command": "да"}, {"label": "Отмена", "command": "нет"}]
                if confirmed else []}

    @blueprint.get("/api/undo")
    def available_undo():
        return {"action": last_undo(uid())}

    @blueprint.post("/api/undo/<int:action_id>")
    def apply_undo(action_id):
        return undo_action(uid(), action_id)

    @blueprint.get("/api/library/reminders/<int:reminder_id>/alerts")
    def reminder_alerts(reminder_id):
        from core.library_store import get_saved_reminder

        if not get_saved_reminder(uid(), reminder_id):
            raise LookupError("Напоминание не найдено.")
        return alert_preferences(uid(), reminder_id)

    @blueprint.post("/api/library/reminders/<int:reminder_id>/alerts")
    def update_reminder_alerts(reminder_id):
        return save_alert_preferences(uid(), reminder_id, payload())

    @blueprint.post("/api/assistant/reminders/<int:reminder_id>/reschedule")
    def reschedule_from_local_time(reminder_id):
        from datetime import datetime, timezone
        from modules.assistant_commands import user_zone
        from core.reminder_store import reschedule_reminder

        value = payload().get("local_time")
        if not isinstance(value, str) or len(value) != 16:
            raise ValueError("Выбери дату и время.")
        naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
        zone = user_zone(uid())
        candidates = {
            naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
            for fold in (0, 1)
            if naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive
        }
        if len(candidates) != 1:
            raise ValueError("Время попало на перевод часов. Выбери другое, однозначное время.")
        future = candidates.pop()
        if future <= datetime.now(timezone.utc):
            raise ValueError("Новое время должно быть в будущем.")
        result = reschedule_reminder(uid(), reminder_id, future)
        if result is None:
            raise LookupError("Напоминание не найдено.")
        return {"ok": True}

    @blueprint.get("/api/privacy")
    def privacy():
        return privacy_notice()

    @blueprint.get("/api/privacy/export")
    def export():
        encoded = json.dumps(export_local_data(uid()), ensure_ascii=False, indent=2)
        response = Response(encoded, mimetype="application/json")
        response.headers["Content-Disposition"] = 'attachment; filename="personal-secretary-export.json"'
        return response

    @blueprint.post("/api/privacy/delete-ticket")
    def prepare_delete():
        return {**privacy_notice(), "ticket": delete_ticket(uid(), session["sid"])}

    @blueprint.post("/api/privacy/delete")
    def delete_account():
        user_id = uid()
        values = payload()
        purge_local_account(user_id, session["sid"], values.get("ticket"), values.get("confirmation"))
        g.account_deleted = True
        clear_state(user_id)
        g.pop("conversation_states", None)
        session.clear()
        return {"ok": True, "local_data_deleted": True,
                "message": "Локальные данные удалены. События в Google Calendar не изменены."}

    return blueprint
