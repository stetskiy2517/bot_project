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
    from core.db import get_or_create_google_user, get_user_appearance_theme, save_user_timezone
    from playwright.sync_api import sync_playwright, expect
    from werkzeug.serving import make_server

    app = web_app.create_web_app()
    user_id = get_or_create_google_user("appearance-browser", "appearance-browser@example.test", "Appearance Browser")
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
            page.wait_for_function("window.PlannerAppearance && document.documentElement.dataset.appearance === 'auto'")
            page.wait_for_function("document.documentElement.dataset.colorScheme === 'dark'")
            assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(17, 18, 20)"

            page.evaluate(
                """() => {
                    const fixture = document.createElement("section");
                    fixture.id = "appearanceDarkFixture";
                    fixture.innerHTML =
                      '<div class="library-head">' +
                      '<div class="library-tabs"><button class="library-tab active">Задачи</button></div>' +
                      '</div>' +
                      '<select class="planner-task-filter"><option>Все категории</option></select>' +
                      '<div class="planner-task-card">' +
                      '<div class="planner-task-title">Активная задача</div>' +
                      '<div class="planner-task-meta">Прочее · Обычный · 23:00</div>' +
                      '</div>' +
                      '<div class="planner-task-card completed">' +
                      '<div class="planner-task-title">Выполненная задача</div>' +
                      '<div class="planner-task-meta">Прочее · Выполнено</div>' +
                      '</div>';
                    document.body.appendChild(fixture);
                }"""
            )
            assert page.locator("#appearanceDarkFixture .planner-task-card").first.evaluate(
                "el => getComputedStyle(el).backgroundColor"
            ) == "rgb(26, 29, 34)"
            assert page.locator("#appearanceDarkFixture .planner-task-title").first.evaluate(
                "el => getComputedStyle(el).color"
            ) == "rgb(242, 244, 246)"
            assert page.locator("#appearanceDarkFixture .planner-task-meta").first.evaluate(
                "el => getComputedStyle(el).color"
            ) == "rgb(157, 165, 176)"
            assert page.locator("#appearanceDarkFixture .planner-task-card.completed .planner-task-title").evaluate(
                "el => getComputedStyle(el).color"
            ) == "rgb(142, 150, 162)"
            assert page.locator("#appearanceDarkFixture .planner-task-filter").evaluate(
                "el => getComputedStyle(el).backgroundColor"
            ) == "rgb(27, 30, 35)"
            assert page.locator("#appearanceDarkFixture .library-tab.active").evaluate(
                "el => getComputedStyle(el).backgroundColor"
            ) == "rgb(42, 46, 53)"
            page.locator("#appearanceDarkFixture").evaluate("el => el.remove()")

            page.locator("#accountBtn").click()
            page.wait_for_selector('[data-settings-open="appearance"]:not([hidden])')
            page.locator('[data-settings-open="appearance"]').click()
            page.wait_for_selector('#settingsTheme-appearance:not([hidden])')
            expect(page.locator('[data-appearance-choice="auto"]')).to_have_attribute("aria-checked", "true")

            page.locator('[data-appearance-choice="light"]').click()
            page.wait_for_function("document.documentElement.dataset.appearance === 'light'")
            page.wait_for_function("document.documentElement.dataset.colorScheme === 'light'")
            page.wait_for_function("localStorage.getItem('personal-secretary:appearance-theme') === 'light'")
            deadline = time.time() + 3
            while time.time() < deadline and get_user_appearance_theme(user_id) != "light":
                time.sleep(0.05)
            assert get_user_appearance_theme(user_id) == "light"

            page.reload()
            page.wait_for_function("window.PlannerAppearance && document.documentElement.dataset.appearance === 'light'")
            assert page.evaluate("document.documentElement.dataset.colorScheme") == "light"

            page.locator("#accountBtn").click()
            page.wait_for_selector('[data-settings-open="appearance"]:not([hidden])')
            page.locator('[data-settings-open="appearance"]').click()
            page.locator('[data-appearance-choice="auto"]').click()
            page.wait_for_function("document.documentElement.dataset.appearance === 'auto'")
            page.wait_for_function("document.documentElement.dataset.colorScheme === 'dark'")

            page.emulate_media(color_scheme="light")
            page.wait_for_function("document.documentElement.dataset.colorScheme === 'light'")
            deadline = time.time() + 3
            while time.time() < deadline and get_user_appearance_theme(user_id) != "auto":
                time.sleep(0.05)
            assert get_user_appearance_theme(user_id) == "auto"

            browser.close()
    finally:
        server.shutdown()
        temporary.cleanup()

    print(f"appearance theme browser test: OK ({args.engine})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
