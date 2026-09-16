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

    result = {
        "browser": "webkit",
        "device": "iPhone 13",
        "passed": False,
        "errors": [],
    }

    browser = None
    context = None
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
            browser = playwright.webkit.launch(headless=True)
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

            page.goto(base, wait_until="domcontentloaded")
            page.wait_for_function(
                "window.PlannerRequests && !document.getElementById('login').classList.contains('open')"
            )
            page.wait_for_function(
                "document.getElementById('accountEmail').textContent.includes('@example.test')"
            )

            page.evaluate("showChat()")
            page.locator("#message").fill("заметка: WebKit smoke")
            page.locator("#message").press("Enter")
            page.wait_for_function("!sendingChat")

            notes = list_notes(user_id)
            if len(notes) != 1 or "WebKit smoke" not in str(notes[0].get("text") or ""):
                raise AssertionError(f"Expected one WebKit smoke note, got: {notes!r}")
            if page_errors:
                raise AssertionError(f"WebKit page errors: {page_errors!r}")

            result.update(
                passed=True,
                user_agent=page.evaluate("navigator.userAgent"),
                viewport=page.viewport_size,
            )
            page.screenshot(path=str(output / "webkit-iphone-smoke.png"), full_page=True)
    except Exception as exc:
        result["errors"].append(str(exc))
        if context is not None:
            try:
                pages = context.pages
                if pages:
                    pages[-1].screenshot(path=str(output / "webkit-iphone-failure.png"), full_page=True)
            except Exception:
                pass
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()
        server.shutdown()
        thread.join(timeout=5)
        temporary.cleanup()
        (output / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
