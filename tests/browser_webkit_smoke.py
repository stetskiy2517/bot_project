"""Small WebKit/iPhone regression against the real Flask app.

This intentionally covers a short deterministic path. The full Chromium suite remains
responsible for broad product coverage; this script catches WebKit-only breakage in the
main mobile interaction path without requiring external providers or user data.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _checkpoint(message: str) -> None:
    print(f"[webkit-smoke] {message}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evidence/browser-webkit-ios")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    temporary = tempfile.TemporaryDirectory()
    os.environ["DB_PATH"] = str(Path(temporary.name) / "webkit.db")
    os.environ["WEB_PUSH_WORKER_ENABLED"] = "0"
    os.environ["EMAIL_AUTO_WORKER_ENABLED"] = "0"
    os.environ["WEB_SESSION_SECRET"] = secrets.token_hex(32)
    os.environ["BASE_URL"] = ""

    _checkpoint("importing application")
    import web_app
    from core.db import get_or_create_google_user, save_user_timezone
    from core.note_store import list_notes
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    app = web_app.create_web_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _checkpoint(f"local server started on {base}")

    result = {
        "browser": "webkit",
        "device": "iPhone 13",
        "passed": False,
        "errors": [],
    }

    try:
        label = secrets.token_hex(8)
        user_id = get_or_create_google_user(label, label + "@example.test", "WebKit User")
        save_user_timezone(user_id, "Europe/Moscow")
        session_value = app.session_interface.get_signing_serializer(app).dumps(
            {
                "user_id": user_id,
                "auth_time": time.time(),
                "csrf_token": secrets.token_urlsafe(32),
                "_permanent": True,
            }
        )

        with sync_playwright() as playwright:
            _checkpoint("launching WebKit")
            browser = playwright.webkit.launch(headless=True)
            context = None
            try:
                device = dict(playwright.devices["iPhone 13"])
                context = browser.new_context(**device, accept_downloads=True)
                context.add_cookies(
                    [
                        {
                            "name": "session",
                            "value": session_value,
                            "url": base,
                            "httpOnly": True,
                            "sameSite": "Lax",
                        }
                    ]
                )
                page = context.new_page()
                page.set_default_timeout(10000)
                page_errors: list[str] = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))

                _checkpoint("opening authenticated app")
                page.goto(base, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_function(
                    "window.PlannerRequests && !document.getElementById('login').classList.contains('open')",
                    timeout=10000,
                )
                page.wait_for_function(
                    "document.getElementById('accountEmail').textContent.includes('@example.test')",
                    timeout=10000,
                )
                page.wait_for_function(
                    "!document.getElementById('mobileBottomNav').hidden && "
                    "document.getElementById('mobileBottomNav').getBoundingClientRect().height > 0",
                    timeout=10000,
                )
                _checkpoint("authenticated mobile shell ready")

                _checkpoint("checking distinct tasks and notes routes on WebKit")
                page.locator('#mobileBottomNav [data-view="more"]').click()
                saved_label = page.locator('[data-more="saved"] .mobile-more-title').inner_text().strip()
                if saved_label != "Заметки":
                    raise AssertionError(f"Saved row must be named 'Заметки', got {saved_label!r}")

                page.locator('[data-more="tasks"]').click()
                page.wait_for_function(
                    "document.getElementById('libraryTasksTab') && "
                    "document.getElementById('libraryTasksTab').classList.contains('active') && "
                    "document.querySelector('#libraryList .planner-task-toolbar')",
                    timeout=10000,
                )
                reminders_display = page.locator("#libraryRemindersTab").evaluate(
                    "element => getComputedStyle(element).display"
                )
                tasks_display = page.locator("#libraryTasksTab").evaluate(
                    "element => getComputedStyle(element).display"
                )
                if reminders_display != "none":
                    raise AssertionError(
                        f"Reminder tab must be hidden in unified tasks view, got display={reminders_display!r}"
                    )
                if tasks_display == "none":
                    raise AssertionError("Tasks tab is hidden in unified tasks view")

                page.locator("#libraryBackBtn").click()
                page.wait_for_function(
                    "!document.getElementById('app').classList.contains('library-active')",
                    timeout=5000,
                )
                page.locator('[data-more="saved"]').click()
                page.wait_for_function(
                    "document.getElementById('app').classList.contains('library-active') && "
                    "document.getElementById('libraryNotesTab').classList.contains('active') && "
                    "document.getElementById('libraryNotesTab').getAttribute('aria-selected') === 'true' && "
                    "!document.getElementById('libraryTasksTab').classList.contains('active') && "
                    "document.getElementById('libraryTasksTab').getAttribute('aria-selected') !== 'true'",
                    timeout=10000,
                )
                if page.locator("#libraryList .planner-task-toolbar").count():
                    raise AssertionError("Notes route incorrectly rendered the tasks toolbar")
                page.locator("#libraryBackBtn").click()
                _checkpoint("tasks and notes routes are distinct")

                page.locator('#mobileBottomNav [data-view="chat"]').click()
                page.wait_for_function(
                    "document.getElementById('app').classList.contains('mobile-view-chat')",
                    timeout=5000,
                )
                page.locator("#message").fill("заметка: WebKit smoke")
                _checkpoint("submitting deterministic note")
                page.locator("#message").press("Enter")
                page.wait_for_function("!sendingChat", timeout=15000)

                notes = list_notes(user_id)
                if len(notes) != 1 or "WebKit smoke" not in str(notes[0].get("text") or ""):
                    raise AssertionError(f"Expected one WebKit smoke note, got: {notes!r}")
                voice_display = page.locator(".voice-shell").evaluate(
                    "element => getComputedStyle(element).display"
                )
                chat_display = page.locator(".chat-shell").evaluate(
                    "element => getComputedStyle(element).display"
                )
                if voice_display != "none":
                    raise AssertionError(
                        f"Voice layer must be hidden in mobile chat, got display={voice_display!r}"
                    )
                if chat_display == "none":
                    raise AssertionError("Chat layer is hidden in mobile chat view")
                if page_errors:
                    raise AssertionError(f"WebKit page errors: {page_errors!r}")
                _checkpoint("note flow and mobile layer isolation verified")

                result.update(
                    passed=True,
                    user_agent=page.evaluate("navigator.userAgent"),
                    viewport=page.viewport_size,
                )
                page.screenshot(path=str(output / "webkit-iphone-smoke.png"), full_page=True)
                _checkpoint("evidence captured")
            except Exception as exc:
                result["errors"].append(str(exc))
                _checkpoint(f"failed: {type(exc).__name__}: {exc}")
                if context is not None:
                    try:
                        pages = context.pages
                        if pages:
                            pages[-1].screenshot(
                                path=str(output / "webkit-iphone-failure.png"), full_page=True
                            )
                    except Exception:
                        pass
            finally:
                if context is not None:
                    _checkpoint("closing browser context")
                    context.close()
                _checkpoint("closing WebKit")
                browser.close()
                _checkpoint("WebKit closed")
    finally:
        _checkpoint("stopping local server")
        server.shutdown()
        thread.join(timeout=5)
        temporary.cleanup()
        (output / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _checkpoint("cleanup complete")

    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
