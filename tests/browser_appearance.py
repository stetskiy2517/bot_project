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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("chromium", "webkit"), default="chromium")
    args = parser.parse_args()

    temporary = tempfile.TemporaryDirectory()
    os.environ["DB_PATH"] = str(Path(temporary.name) / "appearance-browser.db")
    os.environ["WEB_PUSH_WORKER_ENABLED"] = "0"
    os.environ["EMAIL_AUTO_WORKER_ENABLED"] = "0"
    os.environ["WEB_SESSION_SECRET"] = secrets.token_hex(32)
    os.environ["BASE_URL"] = ""

    import web_app
    from core.db import get_or_create_google_user, save_user_timezone
    from playwright.sync_api import sync_playwright, expect
    from werkzeug.serving import make_server

    app = web_app.create_web_app()
    user_id = get_or_create_google_user(
        "appearance-browser",
        "appearance-browser@example.test",
        "Appearance Browser",
    )
    save_user_timezone(user_id, "Europe/Moscow")
    cookie = app.session_interface.get_signing_serializer(app).dumps({
        "user_id": user_id,
        "auth_time": time.time(),
        "csrf_token": secrets.token_urlsafe(32),
        "_permanent": True,
    })

    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with sync_playwright() as p:
            browser_type = p.chromium if args.engine == "chromium" else p.webkit
            browser = browser_type.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 390, "height": 844},
                color_scheme="dark",
                has_touch=True,
                is_mobile=True,
            )
            context.add_cookies([{
                "name": "session",
                "value": cookie,
                "url": base,
                "httpOnly": True,
                "sameSite": "Lax",
            }])
            page = context.new_page()
            page.goto(base)
            page.wait_for_selector("#accountBtn")

            assert page.locator('meta[name="color-scheme"]').get_attribute("content") == "light"
            assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "light"
            assert page.evaluate("typeof window.PlannerAppearance") == "undefined"
            assert "appearance.css" not in page.content()
            assert "appearance.js" not in page.content()

            page.locator("#accountBtn").click()
            expect(page.locator('[data-settings-open="appearance"]')).to_have_count(0)
            expect(page.locator("#appearanceThemeControl")).to_have_count(0)

            before = page.evaluate("getComputedStyle(document.body).backgroundColor")
            page.emulate_media(color_scheme="light")
            page.wait_for_timeout(100)
            assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "light"
            page.emulate_media(color_scheme="dark")
            page.wait_for_timeout(100)
            assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "light"
            assert page.evaluate("getComputedStyle(document.body).backgroundColor") == before

            browser.close()
    finally:
        server.shutdown()
        temporary.cleanup()

    print(f"stable light appearance browser test: OK ({args.engine})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
