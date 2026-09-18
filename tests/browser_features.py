"""Browser-to-Flask tests. External calendar calls are stubbed; local API and DB are real."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evidence/browser-features")
    parser.add_argument("--offline", action="store_true", help="Real Flask handlers, simulated fetch/storage; no HTTP")
    parser.add_argument("--case", default="")
    parser.add_argument("--desktop-only", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory()
    os.environ["DB_PATH"] = str(Path(temporary.name) / "browser.db")
    os.environ["WEB_PUSH_WORKER_ENABLED"] = "0"
    os.environ["WEB_SESSION_SECRET"] = secrets.token_hex(32)
    os.environ["BASE_URL"] = ""

    import web_app
    from core.db import get_or_create_google_user, save_user_timezone, get_google_account
    from core.note_store import create_note, list_notes
    from core.reminder_store import create_reminder
    from core.assistant_preferences import get_assistant_preferences
    from core.notification_policy import get_policy
    from modules.command_templates import list_templates
    from playwright.sync_api import sync_playwright, expect
    from werkzeug.serving import make_server

    app = web_app.create_web_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    events = []
    results = []

    def inserted(user, event):
        events.append((user, event))
        return {**event, "id": f"test-{len(events)}"}

    executor = ThreadPoolExecutor(max_workers=1)
    clients = {}
    offline_modes = {}

    def seed(context):
        label = secrets.token_hex(8)
        user = get_or_create_google_user(label, label + "@example.test", "Browser User")
        save_user_timezone(user, "Europe/Moscow")
        value = app.session_interface.get_signing_serializer(app).dumps({
            "user_id": user, "auth_time": time.time(), "csrf_token": secrets.token_urlsafe(32),
            "_permanent": True,
        })
        context.add_cookies([{"name": "session", "value": value, "url": base, "httpOnly": True, "sameSite": "Lax"}])
        client = app.test_client()
        client.set_cookie("session", value)
        clients[context] = client
        return user

    def load_page(page):
        if not args.offline:
            page.goto(base)
            return
        import re
        client = clients[page.context]
        mode = offline_modes.setdefault(page.context, {"lose": False})
        storage = page.evaluate("window.__testStorage || {}")
        page.goto("about:blank")
        def bridge(_source, path, method, body, headers):
            response = executor.submit(client.open, path, method=method, data=body,
                                       headers={**headers, "Origin": "http://localhost"}).result()
            if mode["lose"] and path == "/api/chat":
                mode["lose"] = False
                return {"lost": True}
            return {"status": response.status_code, "data": response.get_json(silent=True)}
        if not mode.get("bound"):
            page.expose_binding("featureRequest", bridge)
            mode["bound"] = True
        page.evaluate("""data => {
            window.__testStorage = data;
            const storage = {
                getItem: key => window.__testStorage[key] ?? null,
                setItem: (key, value) => {window.__testStorage[key] = String(value);},
                removeItem: key => {delete window.__testStorage[key];},
            };
            Object.defineProperty(window, "localStorage", {value: storage, configurable: true});
            window.fetch = async (path, options = {}) => {
                const headers = Object.fromEntries(new Headers(options.headers || {}));
                const reply = await window.featureRequest(String(path), options.method || "GET", options.body || null, headers);
                if (reply.lost) throw TypeError("Simulated lost response");
                return new Response(JSON.stringify(reply.data), {status: reply.status, headers: {"Content-Type":"application/json"}});
            };
        }""", storage)
        html = (Path(__file__).resolve().parents[1] / "web/index.html").read_text()
        html = re.sub(r"<link\b[^>]*>", "", html)
        web = Path(__file__).resolve().parents[1] / "web"
        helper = "<script>" + (web / "reliability.js").read_text() + "</script>"
        html = html.replace("    <script>\n", helper + "    <script>\n", 1)
        scripts = "".join(
            "<script>" + (web / name).read_text() + "</script>"
            for name in ("reminders.js", "library.js", "voice_gesture.js", "assistant.js", "settings-themes.js")
        )
        page.set_content(html.replace("</body>", scripts + "</body>"), wait_until="load")

    def loaded(page):
        load_page(page)
        page.wait_for_function("window.PlannerRequests && !document.getElementById('login').classList.contains('open')")
        page.wait_for_function("document.getElementById('accountEmail').textContent.includes('@example.test')")

    def command(page, text):
        page.evaluate("showChat()")
        page.locator("#message").fill(text)
        page.locator("#message").press("Enter")
        page.wait_for_function("!sendingChat")

    def settings(page, summary):
        page.locator("#accountBtn").click()
        target = page.locator("#assistantSettings summary", has_text=summary).first
        theme = target.evaluate("el => el.closest('[data-settings-theme]')?.dataset.settingsTheme || ''")
        assert theme, f"No settings theme found for {summary}"
        page.locator(f'[data-settings-open="{theme}"]').click()
        expect(page.locator("#settingsPanel .sheet-head h2")).not_to_have_text("Аккаунт")
        target.click()
        page.wait_for_function("document.getElementById('privacyNotice').textContent.length > 0")

    def draft_retry(page, user):
        loaded(page)
        def lose(route):
            route.fetch()
            route.abort("failed")
        if args.offline:
            offline_modes[page.context]["lose"] = True
        else:
            page.route("**/api/chat", lose, times=1)
        command(page, "заметка: проверить безопасный повтор")
        expect(page.get_by_role("button", name="Проверить / повторить", exact=True)).to_be_visible()
        assert len(list_notes(user)) == 1
        old = page.evaluate("PlannerRequests.draft().id")
        load_page(page) if args.offline else page.reload()
        page.wait_for_function("!!window.PlannerRequests && !!PlannerRequests.draft()?.pending")
        expect(page.locator("#message")).to_have_value("заметка: проверить безопасный повтор")
        assert page.evaluate("PlannerRequests.draft().id") == old
        page.get_by_role("button", name="Проверить / повторить", exact=True).click()
        page.wait_for_function("!sendingChat && !PlannerRequests.draft()")
        assert len(list_notes(user)) == 1

    def free_window(page, user):
        loaded(page)
        previous = len(events)
        command(page, "когда завтра есть 40 минут")
        choice = page.locator("#chat .action").first
        expect(choice).to_be_visible()
        choice.click()
        page.wait_for_function("!sendingChat")
        command(page, "Проверка окна")
        assert len(events) == previous
        page.get_by_role("button", name="Поставить", exact=True).click()
        page.wait_for_function("!sendingChat")
        assert len(events) == previous + 1
        event = events[-1][1]
        assert datetime.fromisoformat(event["end"]["dateTime"]) - datetime.fromisoformat(event["start"]["dateTime"]) == timedelta(minutes=40)

    def settings_navigation(page, user):
        loaded(page)
        page.locator("#accountBtn").click()
        expect(page.locator("#settingsPanel .sheet-head h2")).to_have_text("Аккаунт")
        expect(page.locator("#settingsThemes .settings-theme-link")).to_have_count(5)
        expect(page.locator("#settingsTheme-planning")).to_be_hidden()
        page.locator('[data-settings-open="planning"]').click()
        expect(page.locator("#settingsPanel .sheet-head h2")).to_have_text("Планирование")
        expect(page.locator("#settingsTheme-planning")).to_be_visible()
        expect(page.locator("#settingsThemes")).to_be_hidden()
        page.locator("#settingsThemeBack").click()
        expect(page.locator("#settingsPanel .sheet-head h2")).to_have_text("Аккаунт")
        expect(page.locator("#settingsThemes")).to_be_visible()
        expect(page.locator("#settingsTheme-planning")).to_be_hidden()

    def templates(page, user):
        loaded(page)
        settings(page, "Избранные команды")
        form = page.locator("#templateForm")
        form.locator("[name=name]").fill("Обычный созвон")
        form.locator("[name=title]").fill("Созвон отдела")
        form.locator("[name=duration_minutes]").fill("35")
        form.locator("[name=category]").select_option("work")
        form.get_by_role("button", name="Сохранить шаблон").click()
        expect(page.locator("#assistantTemplates")).to_contain_text("Созвон отдела")
        assert list_templates(user)[0]["duration_minutes"] == 35
        page.locator("#assistantTemplates").get_by_role("button", name="Изменить", exact=True).click()
        form.locator("[name=title]").fill("Обновлённый созвон")
        form.get_by_role("button", name="Сохранить шаблон").click()
        expect(page.locator("#assistantTemplates")).to_contain_text("Обновлённый созвон")
        page.locator("#assistantTemplates").get_by_role("button", name="Ввести", exact=True).click()
        expect(page.locator("#message")).to_have_value("Обычный созвон ")
        assert not any(uid == user for uid, event in events)
        command(page, "Обычный созвон завтра в 11")
        assert events[-1][0] == user
        assert events[-1][1]["summary"] == "Обновлённый созвон"

    def review_preferences(page, user):
        reminder = create_reminder(user, "Разобрать отчёт", datetime.now(timezone.utc) - timedelta(minutes=5))
        loaded(page)
        settings(page, "Обзоры и тихие часы")
        for name in ["morning_enabled", "evening_enabled", "quiet_enabled"]:
            expect(page.locator(f"#assistantDeliveryFields [name={name}]")).not_to_be_checked()
            page.locator(f"#assistantDeliveryFields [name={name}]").check()
        page.locator("[name=quiet_start]").fill("23:00")
        page.locator("[name=quiet_end]").fill("07:00")
        page.locator("#saveAssistantDelivery").click()
        expect(page.locator("#assistantSettingsStatus")).to_contain_text("сохранены")
        assert get_assistant_preferences(user)["quiet_enabled"]
        page.locator("#eveningReview").click()
        expect(page.locator("#chat")).to_contain_text("Разобрать отчёт")
        page.get_by_role("button", name="Оставить", exact=True).click()
        expect(page.locator("#chat")).to_contain_text("Оставлено без изменений")

    def reminder_policy(page, user):
        reminder = create_reminder(user, "Повтор проверки", datetime.now(timezone.utc) + timedelta(hours=3))
        loaded(page)
        page.locator("#libraryOpenBtn").click()
        expect(page.locator("#libraryRemindersTab")).to_have_attribute("aria-hidden", "true")
        page.locator("#libraryTasksTab").click()
        card = page.locator(f'.planner-reminder-task[data-reminder-id="{reminder["reminder_id"]}"]')
        expect(card).to_be_visible()
        card.locator("[data-reminder-edit]").click()
        repeats = page.locator(".unified-notification-repeat")
        expect(repeats).to_be_visible()
        repeats.locator("summary").click()
        page.get_by_label("Интервал повторов в минутах").fill("20")
        page.get_by_label("Число повторов, максимум 5").fill("2")
        repeats.get_by_role("button", name="Сохранить повторы", exact=True).click()
        expect(repeats).to_contain_text("Сохранено.")
        assert get_policy(user, reminder["reminder_id"])["max_repeats"] == 2

    def undo(page, user):
        loaded(page)
        command(page, "заметка: отменяемое действие")
        assert len(list_notes(user)) == 1
        settings(page, "Отмена и данные")
        button = page.locator("#undoNoteAction")
        expect(button).to_be_enabled()
        button.click()
        page.wait_for_function("document.getElementById('undoNoteAction').textContent === 'Нет действия для отмены'")
        expect(button).to_be_disabled()
        assert not list_notes(user)

    def export_and_erase(page, user):
        create_note(user, "Секретная тестовая заметка")
        loaded(page)
        settings(page, "Отмена и данные")
        with page.expect_download() as download:
            page.locator("#exportAccount").click()
        saved = output / f"synthetic-export-{user}.json"
        download.value.save_as(saved)
        data = json.loads(saved.read_text())
        assert data["profile"]["user_id"] == user
        assert data["notes"][0]["text"] == "Секретная тестовая заметка"
        assert "google_token" not in saved.read_text()
        page.once("dialog", lambda dialog: dialog.accept("УДАЛИТЬ МОИ ДАННЫЕ"))
        page.locator("#eraseAccount").click()
        if args.offline:
            page.wait_for_function("!document.getElementById('eraseAccount').disabled")
        else:
            expect(page.locator("#login")).to_have_class(__import__("re").compile(r"\bopen\b"))
        assert not get_google_account(user)
        assert not list_notes(user)

    cases = [draft_retry, free_window, settings_navigation, templates, review_preferences, reminder_policy, undo, export_and_erase]
    if args.case:
        cases = [case for case in cases if case.__name__ == args.case]
    viewports = [{"width": 1280, "height": 900}] if args.desktop_only else [{"width": 1280, "height": 900}, {"width": 390, "height": 844}]
    try:
        with ExitStack() as stack:
            for module in ("calendar_actions", "calendar_availability", "calendar_user", "daily_review"):
                stack.enter_context(patch(f"modules.{module}._list_events", return_value=[]))
            for module in ("calendar_actions", "calendar_availability", "calendar_user"):
                stack.enter_context(patch(f"modules.{module}._create_event", side_effect=inserted))
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM_PATH") or None, args=["--no-sandbox"])
                for viewport in viewports:
                    for case in cases:
                        context = browser.new_context(viewport=viewport, accept_downloads=True)
                        page = context.new_page()
                        page.set_default_timeout(7000)
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        user = seed(context)
                        record = {"case": case.__name__, "viewport": viewport, "mode": "offline" if args.offline else "http", "passed": False}
                        try:
                            case(page, user)
                            assert not errors, errors
                            record["passed"] = True
                        except Exception as exc:
                            record["error"] = f"{type(exc).__name__}: {exc}"
                            record["page_errors"] = list(errors)
                            try:
                                record["visible_text"] = page.locator("body").inner_text(timeout=1000)[-1500:]
                            except Exception as diagnostic_exc:
                                record["diagnostic_error"] = f"{type(diagnostic_exc).__name__}: {diagnostic_exc}"
                            try:
                                page.screenshot(path=str(output / f"{case.__name__}-{viewport['width']}.png"), full_page=True)
                            except Exception as screenshot_exc:
                                record["screenshot_error"] = f"{type(screenshot_exc).__name__}: {screenshot_exc}"
                        finally:
                            context.close()
                        results.append(record)
                        print(json.dumps(record, ensure_ascii=False), flush=True)
                browser.close()
    finally:
        executor.shutdown(wait=True)
        server.shutdown()
        thread.join(timeout=5)
        (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    passed = sum(record["passed"] for record in results)
    print(f"FEATURE_BROWSER_RESULTS {passed}/{len(results)}")
    return 0 if passed == len(cases) * len(viewports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
