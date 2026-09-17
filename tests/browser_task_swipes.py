from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
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
    os.environ["DB_PATH"] = str(Path(temporary.name) / "task-swipes-browser.db")

    import web_app
    from core.db import get_or_create_google_user, save_user_timezone
    from core.reminder_store import create_reminder
    from core.task_planner_store import create_planner_task, get_planner_task
    from playwright.sync_api import expect, sync_playwright
    from werkzeug.serving import make_server

    app = web_app.create_web_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        label = secrets.token_hex(8)
        user_id = get_or_create_google_user(label, label + "@example.test", "Task Swipe Test")
        save_user_timezone(user_id, "Europe/Moscow")
        task = create_planner_task(
            user_id,
            "Swipe задача",
            priority="normal",
            category="personal",
            estimate_minutes=20,
            flexible=True,
        )
        reminder = create_reminder(
            user_id,
            "Задача с колокольчиком",
            datetime.now(timezone.utc) + timedelta(hours=3),
        )
        session_value = app.session_interface.get_signing_serializer(app).dumps({
            "user_id": user_id,
            "auth_time": time.time(),
            "csrf_token": secrets.token_urlsafe(32),
            "_permanent": True,
        })

        with sync_playwright() as playwright:
            if args.engine == "webkit":
                browser = playwright.webkit.launch(headless=True)
            else:
                browser = playwright.chromium.launch(
                    headless=True,
                    executable_path=os.environ.get("CHROMIUM_PATH") or None,
                    args=["--no-sandbox"],
                )
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
            page.on("pageerror", lambda error: errors.append(str(error)))

            page.goto(base)
            page.wait_for_function(
                "window.PlannerRequests && !document.getElementById('login').classList.contains('open')"
            )
            page.locator("#libraryOpenBtn").click()
            page.locator("#libraryTasksTab").click()

            planner_row = page.locator(".planner-task-swipe-row", has_text="Swipe задача").first
            reminder_card = page.locator(
                f'.planner-reminder-task[data-reminder-id="{reminder["reminder_id"]}"]'
            )
            expect(planner_row).to_be_visible()
            expect(reminder_card).to_be_visible()
            expect(page.locator("#libraryList .planner-task-actions")).to_have_count(0)
            expect(reminder_card.locator(".planner-task-bell")).to_be_visible()
            expect(reminder_card.locator(".planner-task-bell")).to_have_attribute(
                "aria-label", "Настроить уведомление"
            )

            note_toolbar = page.locator("#notesProductToolbarWrap")
            if note_toolbar.count():
                expect(note_toolbar).to_be_hidden()
            task_search = page.locator("#libraryList .planner-task-filters input").first
            expect(task_search).to_have_attribute("placeholder", "Поиск по задачам")

            hint = page.locator("#plannerTaskSwipeHint")
            expect(hint).to_contain_text("вправо — выполнить")
            expect(hint).to_contain_text("Влево — действия")

            planner_row.evaluate(
                "el => el.dispatchEvent(new WheelEvent('wheel', {deltaX: 120, deltaY: 0, bubbles: true, cancelable: true}))"
            )
            page.wait_for_function(
                "el => Number(el.dataset.offset || 0) < -20",
                arg=planner_row.element_handle(),
            )
            expect(planner_row.locator(".planner-task-swipe-action", has_text="Изменить")).to_be_visible()
            expect(planner_row.locator(".planner-task-swipe-action.danger", has_text="Удалить")).to_be_visible()

            reminder_card.locator(".planner-task-bell").click()
            expect(page.locator("#reminderEditBackdrop")).to_have_class(__import__("re").compile(r"\bopen\b"))
            page.locator("[data-reminder-edit-cancel]").click()

            planner_row = page.locator(".planner-task-swipe-row", has_text="Swipe задача").first
            planner_row.evaluate(
                "el => el.dispatchEvent(new WheelEvent('wheel', {deltaX: -120, deltaY: 0, bubbles: true, cancelable: true}))"
            )
            page.wait_for_timeout(450)
            assert get_planner_task(user_id, int(task["task_id"]))["status"] == "done"

            assert not errors, errors
            context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        temporary.cleanup()


if __name__ == "__main__":
    main()
