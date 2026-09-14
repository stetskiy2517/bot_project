"""Chromium against the real Flask API and isolated SQLite; only external services are faked."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from unittest.mock import patch

_WORK = tempfile.TemporaryDirectory(prefix="secretary-browser-")
os.environ["DB_PATH"] = str(Path(_WORK.name) / "test.db")
os.environ["WEB_PUSH_ENABLED"] = "false"
os.environ["BASE_URL"] = ""
os.environ["WEB_SESSION_SECRET"] = secrets.token_urlsafe(48)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server
import web_app
from core.assistant_store import alert_preferences, assistant_preferences, list_templates
from core.db import conn, db_lock, get_or_create_google_user, save_user_timezone
from core.library_store import get_saved_reminder
from core.note_store import create_note, list_notes
from core.reminder_store import create_reminder
from core.user_operations import create_web_session, confirm_calendar_effect, remember_calendar_effect
from tests.browser_audit import MOCK_MEDIA

EXTERNAL_EVENTS: dict[int, list[dict]] = {}


def calendar_read(user_id, start, end, **kwargs):
    return list(EXTERNAL_EVENTS.get(user_id, []))


def calendar_create(user_id, event):
    saved = remember_calendar_effect(user_id, event)
    saved.setdefault("id", secrets.token_hex(16))
    EXTERNAL_EVENTS.setdefault(user_id, []).append(saved)
    confirm_calendar_effect(user_id, saved)
    return saved


def send(page, text):
    page.locator("#message").fill(text)
    page.locator("#composer").evaluate("element => element.requestSubmit()")
    page.wait_for_function("!sendingChat")


def panel(page, tab=None):
    page.get_by_role("button", name="Помощник", exact=True).click()
    page.wait_for_function("document.querySelector('#assistantContent').textContent.includes('Встреч:')")
    if tab:
        page.locator(f'.assistant-tabs [data-page="{tab}"]').click()


def local_count(user_id):
    return len(list_notes(user_id, limit=500))


def lost_response(page, user_id, context, base):
    before = local_count(user_id)
    calls = []
    def interrupt(route):
        calls.append(route.request.headers.get("idempotency-key"))
        upstream = route.fetch()
        assert upstream.status == 200
        route.abort("failed")
    page.route("**/api/chat", interrupt)
    send(page, "заметка: Проверка потерянного ответа")
    expect(page.locator("#requestDraft")).to_be_visible()
    assert local_count(user_id) == before + 1
    page.unroute("**/api/chat", interrupt)
    page.get_by_role("button", name="Проверить / повторить", exact=True).click()
    expect(page.locator("#requestDraft")).to_be_hidden()
    assert local_count(user_id) == before + 1
    assert len(calls) == 1


def offline_retry_after_reload(page, user_id, context, base):
    before = local_count(user_id)
    page.route("**/api/chat", lambda route: route.abort("failed"))
    send(page, "заметка: Черновик после перезагрузки")
    key = page.evaluate("SecretaryRequests.readDraft().key")
    assert local_count(user_id) == before
    page.unroute("**/api/chat")
    page.reload()
    page.wait_for_function("SecretaryRequests.readDraft() !== null")
    assert page.evaluate("SecretaryRequests.readDraft().key") == key
    page.get_by_role("button", name="Проверить / повторить", exact=True).click()
    expect(page.locator("#requestDraft")).to_be_hidden()
    assert local_count(user_id) == before + 1


def discard_does_not_auto_send(page, user_id, context, base):
    before = local_count(user_id)
    page.route("**/api/chat", lambda route: route.abort("failed"))
    send(page, "заметка: Удаляем только черновик")
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator("#requestDraft").get_by_role("button", name="Убрать", exact=True).click()
    expect(page.locator("#requestDraft")).to_be_hidden()
    page.unroute("**/api/chat")
    page.reload()
    page.wait_for_function("document.querySelector('#categoryColors').children.length === 7")
    assert page.evaluate("SecretaryRequests.readDraft()") is None
    assert local_count(user_id) == before


def draft_blocks_second_command_and_voice(page, user_id, context, base):
    page.route("**/api/chat", lambda route: route.abort("failed"))
    send(page, "заметка: Не отправлено")
    first = page.evaluate("SecretaryRequests.readDraft().key")
    send(page, "заметка: Второй запрос")
    assert page.evaluate("SecretaryRequests.readDraft().key") == first
    page.locator("#chatVoiceBtn").dispatch_event("pointerdown", {
        "pointerId":1, "pointerType":"touch", "button":0, "clientX":10,"clientY":100,
    })
    page.wait_for_timeout(80)
    assert page.evaluate("auditMedia.recorders.length") == 0
    assert not page.evaluate("voicePressActive")


def voice_retry_same_blob(page, user_id, context, base):
    before = local_count(user_id)
    page.route("**/api/voice", lambda route: route.abort("failed"))
    page.locator("#voiceBtn").dispatch_event("pointerdown", {
        "pointerId":7, "pointerType":"touch", "button":0,"clientX":100,"clientY":250,
    })
    page.wait_for_function("recorder && recorder.state === 'recording'")
    page.evaluate("voiceRecordingStartedAt = performance.now() - 550")
    page.locator("#voiceBtn").dispatch_event("pointerup", {
        "pointerId":7, "pointerType":"touch","button":0,"clientX":100,"clientY":250,
    })
    page.wait_for_function("!sendingVoice && SecretaryRequests.readDraft()?.kind === 'voice'")
    assert page.evaluate("SecretaryRequests.currentVoice(SecretaryRequests.readDraft().key) instanceof Blob")
    stored = page.evaluate("localStorage.getItem('secretary.draft.' + SecretaryRequests.readDraft().owner)")
    assert "synthetic audio" not in stored and "blob" not in stored
    page.unroute("**/api/voice")
    page.get_by_role("button", name="Проверить / повторить", exact=True).click()
    expect(page.locator("#requestDraft")).to_be_hidden()
    assert local_count(user_id) == before + 1


def overview_complete_undo(page, user_id, context, base):
    reminder = create_reminder(user_id, "Браузерное напоминание", datetime.now(timezone.utc)-timedelta(minutes=1))
    panel(page)
    row = page.locator(".assistant-item").filter(has=page.get_by_role("heading", name="Браузерное напоминание"))
    row.get_by_role("button", name="Оставить", exact=True).click()
    expect(page.locator("#assistantStatus")).to_have_text("Оставлено без изменений.")
    row.get_by_role("button", name="Выполнено", exact=True).click()
    expect(row).to_have_count(0)
    assert get_saved_reminder(user_id, reminder["reminder_id"])["status"] == "completed"
    page.locator("#assistantClose").click()
    expect(page.locator(".undo-action")).to_be_visible()
    page.locator(".undo-action").click()
    page.wait_for_function("!document.querySelector('.undo-action').disabled")
    assert get_saved_reminder(user_id, reminder["reminder_id"])["status"] != "completed"


def template_crud_and_use(page, user_id, context, base):
    panel(page, "templates")
    page.get_by_label("Имя шаблона", exact=True).fill("Обычный созвон")
    page.get_by_label("Название события", exact=True).fill("Плановый разговор")
    page.get_by_label("Длительность, минут", exact=True).fill("40")
    page.get_by_role("button", name="Сохранить шаблон", exact=True).click()
    expect(page.locator("#assistantStatus")).to_have_text("Шаблон сохранён.")
    assert list_templates(user_id)[0]["duration_minutes"] == 40
    row = page.locator(".assistant-item").filter(has=page.get_by_role("heading", name="Обычный созвон"))
    row.get_by_role("button", name="Изменить", exact=True).click()
    page.get_by_label("Название события", exact=True).fill("Обновлённый разговор")
    page.get_by_role("button", name="Сохранить шаблон", exact=True).click()
    expect(page.locator("#assistantStatus")).to_have_text("Шаблон сохранён.")
    page.get_by_label("Дата и время: Обычный созвон", exact=True).fill("завтра в 15")
    row.get_by_role("button", name="Использовать", exact=True).click()
    expect(page.locator("#assistantDialog")).not_to_be_visible()
    assert not EXTERNAL_EVENTS.get(user_id)
    page.locator(".slot-actions").get_by_role("button", name="Создать", exact=True).click()
    page.wait_for_function("!sendingChat && !SecretaryRequests.readDraft()")
    assert len(EXTERNAL_EVENTS[user_id]) == 1
    assert EXTERNAL_EVENTS[user_id][0]["summary"] == "Обновлённый разговор"
    panel(page, "templates")
    page.on("dialog", lambda dialog: dialog.accept())
    row.get_by_role("button", name="Удалить", exact=True).click()
    expect(row).to_have_count(0)
    assert not list_templates(user_id)


def slots_require_confirmation(page, user_id, context, base):
    send(page, "когда завтра есть 40 минут?")
    expect(page.locator(".slot-actions button")).to_have_count(3)
    page.locator(".slot-actions button").first.click()
    page.wait_for_function("!sendingChat")
    send(page, "Встреча по проекту")
    expect(page.locator(".slot-actions").get_by_role("button", name="Создать", exact=True)).to_be_visible()
    assert not EXTERNAL_EVENTS.get(user_id)
    page.locator(".slot-actions").get_by_role("button", name="Создать", exact=True).click()
    page.wait_for_function("!sendingChat && !SecretaryRequests.readDraft()")
    assert len(EXTERNAL_EVENTS[user_id]) == 1
    item = EXTERNAL_EVENTS[user_id][0]
    assert datetime.fromisoformat(item["end"]["dateTime"]) - datetime.fromisoformat(item["start"]["dateTime"]) == timedelta(minutes=40)


def notification_preferences(page, user_id, context, base):
    reminder = create_reminder(user_id, "Повторы по выбору", datetime.now(timezone.utc)+timedelta(hours=1))
    panel(page, "notifications")
    expect(page.get_by_label("Утренний обзор", exact=True)).not_to_be_checked()
    page.get_by_label("Утренний обзор", exact=True).check()
    page.get_by_label("Тихие часы", exact=True).check()
    page.get_by_role("button", name="Сохранить уведомления", exact=True).click()
    expect(page.locator("#assistantStatus")).to_have_text("Настройки сохранены.")
    assert assistant_preferences(user_id)["quiet_enabled"] is True
    page.get_by_label("Число повторов: 0 — выключено", exact=True).fill("2")
    page.get_by_label("Интервал, минут", exact=True).fill("20")
    page.get_by_role("button", name="Сохранить повторы", exact=True).click()
    expect(page.locator("#assistantStatus")).to_contain_text("Повторы сохранены.")
    assert alert_preferences(user_id, reminder["reminder_id"])["max_repeats"] == 2


def export_and_confirmed_delete(page, user_id, context, base):
    create_note(user_id, "Экспортируется только моя заметка")
    panel(page, "privacy")
    with page.expect_download() as captured:
        page.get_by_role("button", name="Выгрузить данные", exact=True).click()
    data = json.loads(Path(captured.value.path()).read_text())
    assert len(data["data"]["notes"]) == 1
    assert "google_token" not in json.dumps(data)
    page.get_by_role("button", name="Удалить локальный аккаунт", exact=True).click()
    phrase = page.get_by_label("Введи: УДАЛИТЬ МОИ ДАННЫЕ", exact=True)
    phrase.fill("нет")
    page.get_by_role("button", name="Подтверждаю безвозвратное удаление", exact=True).click()
    expect(page.locator("#assistantStatus")).to_contain_text("не совпало")
    assert local_count(user_id) == 1
    phrase.fill("УДАЛИТЬ МОИ ДАННЫЕ")
    page.get_by_role("button", name="Подтверждаю безвозвратное удаление", exact=True).click()
    expect(page.locator("#assistantContent")).to_contain_text("Локальные данные удалены.")
    assert local_count(user_id) == 0
    response = context.request.get(base + "/api/status")
    assert response.status == 401


def safe_template_rendering(page, user_id, context, base):
    panel(page, "templates")
    title = '<img src=x onerror="window.injectionRan=true">'
    page.get_by_label("Имя шаблона", exact=True).fill(title)
    page.get_by_label("Название события", exact=True).fill(title)
    page.get_by_role("button", name="Сохранить шаблон", exact=True).click()
    expect(page.locator("#assistantStatus")).to_have_text("Шаблон сохранён.")
    assert page.locator("#assistantContent img").count() == 0
    assert not page.evaluate("Boolean(window.injectionRan)")
    assert page.locator("#assistantDialog").evaluate("e => e.scrollWidth <= e.clientWidth + 1")


CASES = (
    lost_response, offline_retry_after_reload, discard_does_not_auto_send,
    draft_blocks_second_command_and_voice, voice_retry_same_blob, overview_complete_undo,
    template_crud_and_use, slots_require_confirmation, notification_preferences,
    export_and_confirmed_delete, safe_template_rendering,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("release-evidence/browser-http"))
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app = web_app.create_web_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    with ExitStack() as stack:
        for module in ("modules.calendar_actions", "modules.calendar_availability", "modules.assistant_commands", "modules.calendar_user"):
            stack.enter_context(patch(module + "._list_events", side_effect=calendar_read))
        for module in ("modules.calendar_actions", "modules.calendar_availability", "modules.assistant_commands"):
            stack.enter_context(patch(module + "._create_event", side_effect=calendar_create))
        stack.enter_context(patch("web_app.transcribe_audio", return_value="заметка: Синтетический голос"))
        with sync_playwright() as playwright:
            launch = {"headless":True}
            if os.getenv("CHROMIUM_PATH"):
                launch["executable_path"] = os.environ["CHROMIUM_PATH"]
            browser = playwright.chromium.launch(**launch)
            try:
                for viewport in ({"width":1280,"height":900}, {"width":390,"height":844}):
                    for case in CASES:
                        token = secrets.token_hex(12)
                        user = get_or_create_google_user(token, token+"@example.test", "Browser User")
                        save_user_timezone(user, "Europe/Moscow")
                        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                        create_web_session(user, sid)
                        cookie = app.session_interface.get_signing_serializer(app).dumps(
                            {"user_id":user,"sid":sid,"csrf_token":csrf,"_permanent":True})
                        context = browser.new_context(viewport=viewport, service_workers="block", accept_downloads=True)
                        context.add_cookies([{"name":app.config["SESSION_COOKIE_NAME"], "value":cookie, "url":base,"httpOnly":True,"sameSite":"Lax"}])
                        context.add_init_script(MOCK_MEDIA)
                        page = context.new_page()
                        page.set_default_timeout(7000)
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        try:
                            page.goto(base, wait_until="networkidle")
                            page.wait_for_function("window.SecretaryRequests && document.querySelector('#categoryColors').children.length === 7")
                            case(page, user, context, base)
                            assert not errors, errors
                            if case in (notification_preferences, safe_template_rendering):
                                page.screenshot(path=str(options.output / f"ui-{case.__name__}-{viewport['width']}.png"), full_page=True)
                            result = {"case":case.__name__, "width":viewport["width"],"status":"passed"}
                        except Exception as exc:
                            page.screenshot(path=str(options.output / f"FAILED-{case.__name__}-{viewport['width']}.png"), full_page=True)
                            result = {"case":case.__name__,"width":viewport["width"],"status":"failed","error":str(exc),"page_errors":errors}
                        finally:
                            context.close()
                        print(json.dumps(result,ensure_ascii=False),flush=True)
                        results.append(result)
            finally:
                browser.close()
    server.shutdown()
    thread.join(timeout=5)
    (options.output / "results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2))
    passed = sum(item["status"] == "passed" for item in results)
    print(f"HTTP_BROWSER: {passed}/{len(results)} passed",flush=True)
    return int(passed != len(results))


if __name__ == "__main__":
    sys.exit(main())
