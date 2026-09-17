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
    os.environ["DB_PATH"] = str(Path(temporary.name) / "reminder-editor-browser.db")

    import web_app
    from core.db import get_or_create_google_user, save_user_timezone
    from core.library_store import get_saved_reminder
    from core.reminder_store import create_reminder
    from datetime import datetime, timedelta, timezone
    from modules.reminder_categories import reminder_category
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
        user_id = get_or_create_google_user(label, email, "Reminder Editor Test")
        save_user_timezone(user_id, "Europe/Moscow")
        reminder = create_reminder(
            user_id,
            "Позвонить клиенту",
            datetime.now(timezone.utc) + timedelta(hours=2),
        )
        reminder_id = reminder["reminder_id"]
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

            page.goto(base)
            page.wait_for_function(
                "window.PlannerRequests && !document.getElementById('login').classList.contains('open')"
            )
            page.wait_for_function(
                "expected => document.getElementById('accountEmail')?.textContent === expected",
                arg=email,
            )
            page.wait_for_function(
                "!document.getElementById('mobileBottomNav').hidden && "
                "document.getElementById('mobileBottomNav').getBoundingClientRect().height > 0"
            )

            page.locator('#mobileBottomNav [data-view="more"]').click()
            expect(page.locator("#mobileMoreScreen")).to_be_visible()
            page.locator('[data-more="saved"]').click()
            expect(page.locator("#libraryScreen")).to_be_visible()

            # Reminders are no longer a separate user-facing section. They are
            # lightweight tasks with an attached notification inside Tasks.
            expect(page.locator("#libraryRemindersTab")).to_be_hidden()
            page.locator("#libraryTasksTab").click()
            card = page.locator(f'.planner-reminder-task[data-reminder-id="{reminder_id}"]')
            expect(card).to_be_visible()
            expect(card).to_contain_text("Позвонить клиенту")
            expect(card).to_contain_text("Уведомление")
            expect(card).to_contain_text("Работа")

            edit_button = card.locator(f'[data-reminder-edit="{reminder_id}"]')
            expect(edit_button).to_be_visible()
            edit_button.click()
            expect(page.locator("#reminderEditBackdrop")).to_have_class("reminder-edit-backdrop open")
            expect(page.locator(".reminder-edit-title")).to_have_text("Задача с уведомлением")

            page.locator("#reminderEditText").fill("Принять лекарство")
            page.locator("#reminderEditCategory").select_option("personal")
            page.locator("#reminderEditRepeat").select_option("daily")
            page.locator(f'[data-reminder-edit-save="{reminder_id}"]').click()
            page.wait_for_function("!document.getElementById('reminderEditBackdrop').classList.contains('open')")
            page.wait_for_function(
                f"document.querySelector('.planner-reminder-task[data-reminder-id=\"{reminder_id}\"]')?.textContent.includes('Личное')"
            )

            stored = get_saved_reminder(user_id, reminder_id)
            assert stored["text"] == "Принять лекарство", stored
            assert stored["repeat_rule"] == "daily", stored
            assert reminder_category(stored) == "personal", stored

            page.locator(f'.planner-reminder-task[data-reminder-id="{reminder_id}"] [data-reminder-edit]').click()
            page.locator("#reminderEditCategory").select_option("auto")
            page.locator(f'[data-reminder-edit-save="{reminder_id}"]').click()
            page.wait_for_function("!document.getElementById('reminderEditBackdrop').classList.contains('open')")
            page.wait_for_function(
                f"document.querySelector('.planner-reminder-task[data-reminder-id=\"{reminder_id}\"]')?.textContent.includes('Здоровье')"
            )
            assert reminder_category(get_saved_reminder(user_id, reminder_id)) == "health"

            assert not errors, errors
            context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        temporary.cleanup()


if __name__ == "__main__":
    main()
