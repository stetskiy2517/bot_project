from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Patch marker not found in {path}: {old[:80]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "modules/navigation.py",
    """def resolve_origin(\n    user_id: int,\n    target_event: dict,\n    timezone: str,\n    preferences: dict | None = None,\n) -> str | None:\n    live_origin = current_location_origin(user_id)\n    if live_origin:\n        return live_origin\n""",
    """def resolve_origin(\n    user_id: int,\n    target_event: dict,\n    timezone: str,\n    preferences: dict | None = None,\n    *,\n    prefer_live: bool = True,\n) -> str | None:\n    if prefer_live:\n        live_origin = current_location_origin(user_id)\n        if live_origin:\n            return live_origin\n""",
)
replace_once(
    "modules/navigation.py",
    """    origin = resolve_origin(user_id, source_event, timezone, preferences=prefs)\n""",
    """    prefer_live = source_start <= datetime.now(source_start.tzinfo) + timedelta(hours=3)\n    origin = resolve_origin(\n        user_id,\n        source_event,\n        timezone,\n        preferences=prefs,\n        prefer_live=prefer_live,\n    )\n""",
)

replace_once(
    "web_app.py",
    """from modules.navigation import estimate_route, navigation_configured, navigation_provider\n""",
    """from modules.navigation import estimate_route, navigation_configured, navigation_provider\nfrom modules.navigation_monitor import request_navigation_recalculation, start_navigation_monitor_worker\n""",
)
replace_once(
    "web_app.py",
    """    init_db()\n    start_reminder_push_worker()\n    app = Flask(\"personal-secretary-web\", static_folder=None)\n""",
    """    init_db()\n    start_reminder_push_worker()\n    start_navigation_monitor_worker()\n    app = Flask(\"personal-secretary-web\", static_folder=None)\n""",
)
replace_once(
    "web_app.py",
    """        return {\n            \"ok\": True,\n            \"accuracy\": location.accuracy_meters,\n            \"captured_at\": location.captured_at.isoformat(),\n        }\n""",
    """        request_navigation_recalculation(user_id)\n        return {\n            \"ok\": True,\n            \"accuracy\": location.accuracy_meters,\n            \"captured_at\": location.captured_at.isoformat(),\n        }\n""",
)

replace_once(
    "modules/calendar_actions.py",
    """from modules.calendar_user import (\n    _event_start,\n    _format_event_line,\n    _get_calendar_service,\n    _list_events,\n    _parse_search_period,\n    _user_zone,\n)\n""",
    """from modules.calendar_user import (\n    _event_start,\n    _format_event_line,\n    _get_calendar_service,\n    _list_events,\n    _parse_search_period,\n    _user_zone,\n)\nfrom modules.navigation import safe_delete_travel_for_event, sync_travel_for_event\n""",
)
replace_once(
    "modules/calendar_actions.py",
    """            else:\n                service.events().delete(calendarId=\"primary\", eventId=event[\"id\"]).execute()\n            await update.message.reply_text(f\"Событие «{event.get('summary', 'Без названия')}» удалено.\")\n""",
    """            else:\n                service.events().delete(calendarId=\"primary\", eventId=event[\"id\"]).execute()\n                safe_delete_travel_for_event(user_id, event[\"id\"])\n            await update.message.reply_text(f\"Событие «{event.get('summary', 'Без названия')}» удалено.\")\n""",
)
replace_once(
    "modules/calendar_actions.py",
    """            updated = service.events().patch(**patch_kwargs).execute()\n            await update.message.reply_text(\n""",
    """            updated = service.events().patch(**patch_kwargs).execute()\n            sync_travel_for_event(user_id, updated, pending[\"timezone\"])\n            await update.message.reply_text(\n""",
)
