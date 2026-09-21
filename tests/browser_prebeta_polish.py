from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["WEB_PUSH_WORKER_ENABLED"] = "0"
os.environ["EMAIL_AUTO_WORKER_ENABLED"] = "0"
os.environ["BASE_URL"] = ""
os.environ["WEB_SESSION_SECRET"] = secrets.token_hex(32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("chromium", "webkit"), default="chromium")
    args = parser.parse_args()

    temporary = tempfile.TemporaryDirectory()
    os.environ["DB_PATH"] = str(Path(temporary.name) / "prebeta-polish-browser.db")

    import web_app
    from core.db import get_or_create_google_user, save_user_timezone
    from core.note_store import create_note
    from playwright.sync_api import expect, sync_playwright
    from werkzeug.serving import make_server

    app = web_app.create_web_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        label = secrets.token_hex(8)
        email = label + "@example.test"
        user_id = get_or_create_google_user(label, email, "Prebeta Polish Test")
        save_user_timezone(user_id, "Europe/Moscow")
        create_note(user_id, "Тестовая заметка для проверки возврата свайпом", title="Тестовая заметка")
        session_value = app.session_interface.get_signing_serializer(app).dumps({
            "user_id": user_id,
            "auth_time": time.time(),
            "csrf_token": secrets.token_urlsafe(32),
            "_permanent": True,
        })

        with sync_playwright() as playwright:
            browser_type = playwright.webkit if args.engine == "webkit" else playwright.chromium
            launch_kwargs = {"headless": True}
            if args.engine == "chromium":
                launch_kwargs.update({
                    "executable_path": os.environ.get("CHROMIUM_PATH") or None,
                    "args": ["--no-sandbox"],
                })
            browser = browser_type.launch(**launch_kwargs)
            context = browser.new_context(
                viewport={"width": 390, "height": 844},
                is_mobile=args.engine == "webkit",
                has_touch=args.engine == "webkit",
            )
            context.add_cookies([{
                "name": "session",
                "value": session_value,
                "url": base,
                "httpOnly": True,
                "sameSite": "Lax",
            }])
            page = context.new_page()
            errors: list[str] = []
            dialogs: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))

            page.goto(base)
            page.wait_for_function(
                "window.PlannerRequests && window.PlannerPolish && window.PlannerSettingsThemes && "
                "!document.getElementById('login').classList.contains('open')"
            )
            page.wait_for_function(
                "expected => document.getElementById('accountEmail')?.textContent === expected",
                arg=email,
            )

            # Every handled bottom sheet can be dismissed by a downward gesture from its grab area.
            # Life balance now has its own visible control beside Settings in the top-right corner.
            life_balance_entry = page.locator("#lifeWheelBtn")
            expect(life_balance_entry).to_be_visible()
            expect(life_balance_entry).to_have_attribute("aria-label", "Баланс жизни")
            life_balance_entry.click()
            life_wheel = page.locator("#lifeWheelPanel")
            expect(life_wheel).to_have_class(__import__("re").compile(r"\bopen\b"))
            expect(life_wheel.locator(".life-wheel-sheet")).to_be_visible()
            handle = page.locator("#lifeWheelPanel .handle")
            handle.evaluate("""
                el => {
                  const rect = el.getBoundingClientRect();
                  const x = rect.left + rect.width / 2;
                  const y = rect.top + Math.max(1, rect.height / 2);
                  window.__sheetGesturePoint = {x, y};
                  const eventWithTouches = (type, property, touches) => {
                    const event = new Event(type, {bubbles: true, cancelable: true});
                    Object.defineProperty(event, property, {value: touches});
                    el.dispatchEvent(event);
                  };
                  eventWithTouches('touchstart', 'touches', [{clientX: x, clientY: y}]);
                  eventWithTouches('touchmove', 'touches', [{clientX: x, clientY: y + 90}]);
                }
            """)
            dragged_transform = page.locator("#lifeWheelPanel .life-wheel-sheet").evaluate("el => el.style.transform")
            assert "translate3d" in dragged_transform and "90" in dragged_transform, dragged_transform
            handle.evaluate("""
                el => {
                  const {x, y} = window.__sheetGesturePoint;
                  const event = new Event('touchend', {bubbles: true, cancelable: true});
                  Object.defineProperty(event, 'changedTouches', {value: [{clientX: x, clientY: y + 90}]});
                  el.dispatchEvent(event);
                }
            """)
            expect(life_wheel).not_to_have_class(__import__("re").compile(r"\bopen\b"))

            # Chat keeps exactly the latest 14 messages across auto-collapse and page reload.
            page.locator('#mobileBottomNav [data-view="chat"]').click()
            page.evaluate("""
                () => {
                  const chat = document.getElementById('chat');
                  chat.replaceChildren();
                  for (let index = 1; index <= 16; index += 1) {
                    const item = document.createElement('div');
                    item.className = `msg ${index % 2 ? 'user' : 'assistant'}`;
                    item.textContent = `history-${index}`;
                    chat.appendChild(item);
                  }
                }
            """)
            page.wait_for_function("document.querySelectorAll('#chat > .msg').length === 14")
            assert page.locator("#chat > .msg").first.inner_text() == "history-3"
            assert page.locator("#chat > .msg").last.inner_text() == "history-16"

            # Collapse through the real mobile navigation instead of the hidden desktop control.
            page.locator('#mobileBottomNav [data-view="home"]').click()
            page.wait_for_function("!document.getElementById('app').classList.contains('chat-active')")
            assert page.locator("#chat > .msg").count() == 14
            page.locator('#mobileBottomNav [data-view="chat"]').click()
            page.wait_for_function("document.querySelectorAll('#chat > .msg').length === 14")
            assert page.locator("#chat > .msg").first.inner_text() == "history-3"

            page.reload()
            page.wait_for_function(
                "window.PlannerRequests && window.PlannerPolish && window.PlannerSettingsThemes && "
                "!document.getElementById('login').classList.contains('open')"
            )
            page.wait_for_function(
                "expected => document.getElementById('accountEmail')?.textContent === expected",
                arg=email,
            )
            page.locator('#mobileBottomNav [data-view="chat"]').click()
            page.wait_for_function("document.querySelectorAll('#chat > .msg').length === 14")
            assert page.locator("#chat > .msg").first.inner_text() == "history-3"
            assert page.locator("#chat > .msg").last.inner_text() == "history-16"

            # A first-install clients.claim() controller change must not reload an active screen.
            page.evaluate("""
                window.__claimSentinel = 'alive';
                navigator.serviceWorker?.dispatchEvent(new Event('controllerchange'));
            """)
            page.wait_for_timeout(250)
            assert page.evaluate("window.__claimSentinel") == "alive"

            # Note detail -> right/back semantics: close one sheet and stay in Notes.
            page.locator('#mobileBottomNav [data-view="tasks"]').click()
            expect(page.locator("#libraryScreen")).to_be_visible()
            page.locator("#libraryNotesTab").click()
            expect(page.locator("#libraryNotesTab")).to_have_attribute("aria-selected", "true")
            note = page.locator(".library-card", has_text="Тестовая заметка").first
            expect(note).to_be_visible()
            note.click()
            note_sheet = page.locator("#noteWindowBackdrop")
            expect(note_sheet).to_have_class(__import__("re").compile(r"\bopen\b"))
            page.locator(".note-window").evaluate("""
                el => {
                  const rect = el.getBoundingClientRect();
                  const x = rect.left + rect.width / 2;
                  const y = Math.min(rect.bottom - 36, rect.top + 130);
                  const eventWithTouches = (type, property, touches) => {
                    const event = new Event(type, {bubbles: true, cancelable: true});
                    Object.defineProperty(event, property, {value: touches});
                    el.dispatchEvent(event);
                  };
                  eventWithTouches('touchstart', 'touches', [{clientX: x, clientY: y}]);
                  eventWithTouches('touchend', 'changedTouches', [{clientX: x + 90, clientY: y}]);
                }
            """)
            expect(note_sheet).not_to_have_class(__import__("re").compile(r"\bopen\b"))
            expect(note_sheet).to_be_hidden()
            expect(page.locator("#libraryScreen")).to_be_visible()
            expect(page.locator("#libraryNotesTab")).to_have_attribute("aria-selected", "true")
            page.locator("#libraryBackBtn").click()

            # Account menu has five thematic screens plus the route shortcut.
            page.locator("#accountBtn").click()
            expect(page.locator("#settingsPanel")).to_have_class(__import__("re").compile(r"\bopen\b"))
            expect(page.locator("#settingsThemes .settings-theme-link")).to_have_count(7)
        expect(page.locator('[data-settings-open="appearance"]')).to_be_visible()
            expect(page.locator("#settingsThemes .settings-theme-link[data-settings-utility]")).to_have_count(1)
            expect(page.locator('[data-settings-utility="saved"]')).to_have_count(0)
            expect(page.locator('[data-settings-utility="route"]')).to_contain_text("Маршрут")
            page.locator('[data-settings-open="account"]').click()
            expect(page.locator("#settingsTheme-account")).to_be_visible()
            diagnostics = page.locator("#diagnosticsGroup")
            expect(diagnostics).to_be_visible()
            expect(diagnostics).not_to_have_attribute("open", "")
            expect(diagnostics).not_to_have_class(__import__("re").compile(r"settings-theme-flat-group"))
            diagnostics.locator("summary").click()
            expect(diagnostics).to_have_attribute("open", "")
            page.locator("#refreshDiagnostics").click()
            page.wait_for_function("document.getElementById('diagVersion')?.textContent?.includes('prebeta-v14')")
            expect(page.locator("#diagNetwork")).not_to_have_text("—")
            page.locator("#settingsThemeBack").click()
            expect(page.locator("#settingsThemes")).to_be_visible()
            page.locator("#closeSettings").click()

            # Today empty states explain what to do instead of showing dead ends.
            page.locator('#mobileBottomNav [data-view="today"]').click()
            expect(page.locator("#mobileTodayContent")).to_be_visible()
            page.wait_for_timeout(500)
            content_text = page.locator("#mobileTodayContent").inner_text()
            assert "Срочных задач нет" in content_text, content_text
            assert "В календаре свободно" in content_text or "календарь недоступен" in content_text.lower(), content_text

            # Offline state is visible and recovery is actionable.
            context.set_offline(True)
            page.evaluate("window.dispatchEvent(new Event('offline'))")
            expect(page.locator("#plannerConnectionBanner")).to_have_class(__import__("re").compile(r"\boffline\b"))
            expect(page.locator("#plannerConnectionBanner")).to_contain_text("Нет соединения")
            context.set_offline(False)
            page.evaluate("window.dispatchEvent(new Event('online'))")
            page.wait_for_timeout(400)
            expect(page.locator("#plannerConnectionBanner")).not_to_have_class(__import__("re").compile(r"\boffline\b"))

            assert not dialogs, dialogs
            assert not errors, errors
            context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        temporary.cleanup()


if __name__ == "__main__":
    main()
