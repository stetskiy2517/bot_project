from __future__ import annotations

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
    temporary = tempfile.TemporaryDirectory()
    os.environ["DB_PATH"] = str(Path(temporary.name) / "email-settings-browser.db")

    import web_app
    from core.db import get_or_create_google_user, save_user_timezone
    from playwright.sync_api import expect, sync_playwright
    from werkzeug.serving import make_server

    app = web_app.create_web_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        label = secrets.token_hex(8)
        user_id = get_or_create_google_user(label, label + "@example.test", "Email Settings Test")
        save_user_timezone(user_id, "Europe/Moscow")
        session_value = app.session_interface.get_signing_serializer(app).dumps({
            "user_id": user_id,
            "auth_time": time.time(),
            "csrf_token": secrets.token_urlsafe(32),
            "_permanent": True,
        })

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=os.environ.get("CHROMIUM_PATH") or None,
                args=["--no-sandbox"],
            )
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_cookies([{
                "name": "session",
                "value": session_value,
                "url": base,
                "httpOnly": True,
                "sameSite": "Lax",
            }])
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def delay_email_script(route):
                time.sleep(0.8)
                route.continue_()

            page.route("**/email.js", delay_email_script, times=1)
            page.goto(base, wait_until="load")
            page.wait_for_function(
                "window.PlannerRequests && !document.getElementById('login').classList.contains('open')"
            )
            page.wait_for_function("document.getElementById('accountEmail').textContent.includes('@example.test')")

            expect(page.locator("#emailGroup")).to_have_count(1)
            expect(page.locator("#emailAutoAnalysis")).to_have_count(1)

            page.locator("#accountBtn").click()
            expect(page.locator("#settingsPanel .sheet-head h2")).to_have_text("Настройки")
            expect(page.locator("#emailGroup summary")).to_be_hidden()
            page.locator('[data-settings-open="integrations"]').click()
            expect(page.locator("#settingsPanel .sheet-head h2")).to_have_text("Интеграции")
            expect(page.locator("#emailGroup summary")).to_be_visible()
            expect(page.locator("#emailGroup summary")).to_contain_text("Почта")
            expect(page.locator("#emailGroup")).to_have_attribute("open", "")
            expect(page.locator("#emailGroup")).to_have_class(__import__("re").compile(r"settings-theme-flat-group"))
            expect(page.locator("#emailAutoAnalysis")).to_be_visible()
            expect(page.locator("#emailGroup")).to_contain_text("Автоматически разбирать новые письма")
            expect(page.locator("#emailAutoStatus")).to_contain_text("Выключено")

            assert not errors, errors
            context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        temporary.cleanup()


if __name__ == "__main__":
    main()
