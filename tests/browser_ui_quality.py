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


def assert_target(page, selector: str, minimum: float = 44.0) -> None:
    locator = page.locator(selector).first
    locator.wait_for(state="visible")
    box = locator.bounding_box()
    assert box, f"{selector}: no bounding box"
    assert box["width"] >= minimum - 0.5, f"{selector}: width {box['width']} < {minimum}"
    assert box["height"] >= minimum - 0.5, f"{selector}: height {box['height']} < {minimum}"


def assert_no_horizontal_overflow(page, label: str) -> None:
    values = page.evaluate(
        """() => ({
            viewport: window.innerWidth,
            document: document.documentElement.scrollWidth,
            body: document.body.scrollWidth,
        })"""
    )
    widest = max(values["document"], values["body"])
    assert widest <= values["viewport"] + 1, f"{label}: horizontal overflow {values}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("chromium", "webkit"), default="chromium")
    parser.add_argument("--output", type=Path, default=Path("evidence/browser-ui-quality"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    temporary = tempfile.TemporaryDirectory()
    os.environ["DB_PATH"] = str(Path(temporary.name) / "ui-quality.db")

    import web_app
    from core.db import get_or_create_google_user, save_user_timezone
    from core.navigation_store import set_navigation_enabled
    from core.reminder_store import create_reminder
    from core.task_planner_store import create_planner_task
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
        user_id = get_or_create_google_user(label, email, "UI Quality Test")
        save_user_timezone(user_id, "Europe/Moscow")
        set_navigation_enabled(user_id, True)
        task = create_planner_task(
            user_id,
            "Проверить мобильный интерфейс",
            due_at=datetime.now(timezone.utc) + timedelta(hours=4),
            category="work",
            estimate_minutes=30,
        )
        reminder = create_reminder(
            user_id,
            "Проверить уведомление",
            datetime.now(timezone.utc) + timedelta(hours=5),
        )
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
            sizes = [{"width": 390, "height": 844}]
            if args.engine == "chromium":
                sizes.append({"width": 320, "height": 568})

            for viewport in sizes:
                context = browser.new_context(
                    viewport=viewport,
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
                page.goto(base)
                page.wait_for_function(
                    "window.PlannerLibrary && window.PlannerTaskEditor && window.PlannerReminderEditor && "
                    "!document.getElementById('login').classList.contains('open')"
                )

                assert page.locator("#libraryOpenBtn").evaluate(
                    "el => getComputedStyle(el).display"
                ) == "none"
                assert page.evaluate("document.elementFromPoint(8, 8)?.id || ''") != "libraryOpenBtn"

                for selector in ("#lifeWheelBtn", "#accountBtn"):
                    assert_target(page, selector)
                nav_buttons = page.locator("#mobileBottomNav .mobile-nav-button:visible")
                assert nav_buttons.count() >= 4
                for index in range(nav_buttons.count()):
                    box = nav_buttons.nth(index).bounding_box()
                    assert box and box["height"] >= 47.5, f"bottom nav target too small: {box}"
                assert_no_horizontal_overflow(page, "home")

                page.locator('#mobileBottomNav [data-view="chat"]').click()
                assert_target(page, "#chatVoiceBtn")
                assert_target(page, ".send-button")
                assert_no_horizontal_overflow(page, "chat")

                page.locator('#mobileBottomNav [data-view="today"]').click()
                page.wait_for_function("document.querySelector('#mobileTodayContent .mobile-card')")
                assert_target(page, ".mobile-card-icon-button")
                task_row = page.locator(f'[data-task-id="{task["task_id"]}"]').first
                expect(task_row).to_be_visible()
                task_row.click()
                page.wait_for_function("document.getElementById('mobileSheetBackdrop').classList.contains('open')")
                assert_target(page, "#mobileTaskTitle")
                assert_target(page, "[data-task-save]")
                assert_no_horizontal_overflow(page, "today task sheet")
                page.locator("#mobileSheetBackdrop").dispatch_event("click")

                page.evaluate("() => { window.PlannerTaskEditor.openTask({}); return true; }")
                page.wait_for_function("document.querySelector('.task-editor-backdrop.open')")
                assert_target(page, ".task-editor-close")
                page.locator(".task-editor-close").click()

                page.evaluate(
                    "(id) => { window.PlannerReminderEditor.open(id); return true; }",
                    reminder["reminder_id"],
                )
                page.wait_for_function("document.querySelector('.reminder-edit-backdrop.open')")
                assert_target(page, ".reminder-edit-back")
                page.locator(".reminder-edit-back").click()

                page.locator("#accountBtn").click()
                expect(page.locator("#settingsPanel")).to_have_class(__import__("re").compile(r"\bopen\b"))
                assert_target(page, "#closeSettings")
                origin_style = page.locator("#navigationOriginQuestion").get_attribute("style") or ""
                optimization_style = page.locator("#navigationOptimizationQuestion").get_attribute("style") or ""
                assert "100dvh" in origin_style and "overflow-y: auto" in origin_style
                assert "100dvh" in optimization_style and "overflow-y: auto" in optimization_style
                page.locator("#closeSettings").click()

                page.route(
                    "**/api/navigation/next-route",
                    lambda route: (time.sleep(0.15), route.fulfill(
                        status=200,
                        content_type="application/json",
                        body='{"url":"https://yandex.ru/maps/?mode=routes&rtext=~55.75%2C37.61&rtt=auto","title":"Тест","destination":"Точка"}',
                    )),
                )
                page.evaluate(
                    """() => {
                        window.__routeWindowEvents = [];
                        window.open = (url) => {
                            window.__routeWindowEvents.push({kind: 'open', url});
                            return {
                                opener: null,
                                location: {replace: value => window.__routeWindowEvents.push({kind: 'replace', url: value})},
                                close: () => window.__routeWindowEvents.push({kind: 'close'}),
                            };
                        };
                        document.getElementById('openNextRoute').click();
                        window.__routeWindowEvents.push({kind: 'after-click'});
                    }"""
                )
                page.wait_for_function("window.__routeWindowEvents.some(item => item.kind === 'replace')")
                route_events = page.evaluate("window.__routeWindowEvents")
                assert route_events[0] == {"kind": "open", "url": "about:blank"}, route_events
                assert route_events[1]["kind"] == "after-click", route_events
                page.unroute("**/api/navigation/next-route")

                page.evaluate("window.PlannerLibrary.open('tasks')")
                page.wait_for_function(
                    """id => document.querySelector('[data-reminder-id="' + id + '"]')""",
                    arg=reminder["reminder_id"],
                )
                detail_path = f"**/api/mobile/reminders/{reminder['reminder_id']}/details"
                page.route(
                    detail_path,
                    lambda route: route.fulfill(
                        status=503,
                        content_type="application/json",
                        body='{"error":"temporary","message":"Тестовая ошибка загрузки"}',
                    ),
                )
                page.locator(f'[data-reminder-edit="{reminder["reminder_id"]}"]').first.click()
                expect(page.locator(".library-toast")).to_have_class(__import__("re").compile(r"\bshow\b"))
                expect(page.locator(".library-toast")).to_contain_text("Тестовая ошибка загрузки")
                page.unroute(detail_path)

                assert_no_horizontal_overflow(page, "library")
                page.screenshot(
                    path=str(args.output / f"{args.engine}-{viewport['width']}x{viewport['height']}.png"),
                    full_page=True,
                )
                context.close()

            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        temporary.cleanup()


if __name__ == "__main__":
    main()
