from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["WEB_PUSH_WORKER_ENABLED"] = "0"
os.environ["EMAIL_AUTO_WORKER_ENABLED"] = "0"
os.environ["BASE_URL"] = ""
os.environ["WEB_SESSION_SECRET"] = secrets.token_hex(32)


VIEWPORTS = (
    {"width": 320, "height": 568},
    {"width": 360, "height": 800},
    {"width": 390, "height": 844},
    {"width": 430, "height": 932},
)


def assert_no_horizontal_overflow(page, label: str) -> None:
    metrics = page.evaluate("""() => ({
      viewport: window.innerWidth,
      html: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
      app: document.getElementById('app')?.scrollWidth || 0,
    })""")
    limit = metrics["viewport"] + 1
    assert metrics["html"] <= limit, f"{label}: html overflow {metrics}"
    assert metrics["body"] <= limit, f"{label}: body overflow {metrics}"
    # The app intentionally keeps the library panel one viewport off-screen for
    # swipe navigation, so app.scrollWidth can be exactly 2x viewport without
    # creating document-level horizontal scrolling.


def assert_touch_targets(page, selectors: list[str], label: str) -> None:
    bad = page.evaluate(
        """selectors => {
          const result = [];
          for (const selector of selectors) {
            for (const node of document.querySelectorAll(selector)) {
              const style = getComputedStyle(node);
              if (
                style.display === 'none' ||
                style.visibility === 'hidden' ||
                style.pointerEvents === 'none' ||
                node.closest('[hidden]')
              ) continue;
              const rect = node.getBoundingClientRect();
              if (rect.width < 1 || rect.height < 1) continue;
              if (rect.width < 44 || rect.height < 44) {
                result.push({
                  selector,
                  id: node.id || '',
                  cls: node.className || '',
                  width: Math.round(rect.width * 10) / 10,
                  height: Math.round(rect.height * 10) / 10,
                  text: String(node.textContent || '').trim().slice(0, 40),
                });
              }
            }
          }
          return result;
        }""",
        selectors,
    )
    assert not bad, f"{label}: touch targets below 44px: {bad}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evidence/layout-audit"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    temporary = tempfile.TemporaryDirectory()
    os.environ["DB_PATH"] = str(Path(temporary.name) / "layout-audit.db")

    import web_app
    from core.db import get_or_create_google_user, save_user_timezone
    from core.note_store import create_note
    from core.reminder_store import create_reminder
    from core.task_planner_store import create_planner_task
    from playwright.sync_api import expect, sync_playwright
    from werkzeug.serving import make_server
    from datetime import datetime, timedelta, timezone

    app = web_app.create_web_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    token = secrets.token_hex(8)
    user_id = get_or_create_google_user(
        f"layout-{token}",
        f"layout-{token}@example.test",
        "Layout Audit",
    )
    save_user_timezone(user_id, "Europe/Moscow")
    create_note(
        user_id,
        "Длинный текст заметки для проверки реального мобильного интерфейса и переносов строк.",
        title="Тестовая заметка с длинным названием",
    )
    create_reminder(
        user_id,
        "Позвонить клиенту и согласовать документы",
        datetime.now(timezone.utc) + timedelta(hours=4),
    )
    create_planner_task(
        user_id,
        "Подготовить очень длинный квартальный отчёт для руководителя",
        description="Описание задачи для проверки карточки и редактора на узком экране.",
        due_at=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        priority="high",
        category="work",
        estimate_minutes=90,
        flexible=True,
    )

    session_value = app.session_interface.get_signing_serializer(app).dumps({
        "user_id": user_id,
        "auth_time": time.time(),
        "csrf_token": secrets.token_urlsafe(32),
        "_permanent": True,
    })

    results = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=os.environ.get("CHROMIUM_PATH") or None,
                args=["--no-sandbox"],
            )
            try:
                for viewport in VIEWPORTS:
                    context = browser.new_context(viewport=viewport, service_workers="block")
                    context.add_cookies([{
                        "name": "session",
                        "value": session_value,
                        "url": base,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }])
                    page = context.new_page()
                    page.set_default_timeout(8000)
                    errors: list[str] = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    try:
                        page.goto(base, wait_until="networkidle")
                        page.wait_for_function(
                            "window.PlannerLibrary && window.PlannerPolish && "
                            "document.getElementById('mobileBottomNav') && "
                            "!document.getElementById('login').classList.contains('open')"
                        )
                        width = viewport["width"]
                        assert_no_horizontal_overflow(page, f"home-{width}")
                        assert_touch_targets(
                            page,
                            [
                                "#accountBtn",
                                "#lifeWheelBtn",
                                "#mobileTodayAdd",
                                ".mobile-nav-button",
                            ],
                            f"home-{width}",
                        )
                        # Chat composer must stay usable on narrow phones and must not
                        # trigger Safari auto-zoom due to undersized controls.
                        page.locator('#mobileBottomNav [data-view="chat"]').click()
                        page.wait_for_function(
                            "document.getElementById('app').classList.contains('mobile-view-chat')"
                        )
                        assert_no_horizontal_overflow(page, f"chat-{width}")
                        assert_touch_targets(
                            page,
                            ["#chatVoiceBtn", ".send-button"],
                            f"chat-{width}",
                        )
                        composer_font = page.locator("#message").evaluate(
                            "node => parseFloat(getComputedStyle(node).fontSize)"
                        )
                        assert composer_font >= 16, f"chat-{width}: input font {composer_font}px can trigger iOS zoom"
                        page.locator('#mobileBottomNav [data-view="home"]').click()

                        hidden_library = page.locator("#libraryOpenBtn").evaluate(
                            """node => {
                              const style = getComputedStyle(node);
                              const rect = node.getBoundingClientRect();
                              return {
                                display: style.display,
                                pointer: style.pointerEvents,
                                width: rect.width,
                                height: rect.height,
                              };
                            }"""
                        )
                        assert (
                            hidden_library["display"] == "none"
                            or hidden_library["pointer"] == "none"
                            or hidden_library["width"] == 0
                            or hidden_library["height"] == 0
                        ), f"Hidden library control can intercept taps: {hidden_library}"

                        # Today.
                        page.locator('#mobileBottomNav [data-view="today"]').click()
                        page.wait_for_function(
                            "!document.querySelector('#mobileTodayContent .mobile-loading')"
                        )
                        assert_no_horizontal_overflow(page, f"today-{width}")
                        assert_touch_targets(
                            page,
                            [
                                "#mobileTodayAdd",
                                "#mobileTodayContent .mobile-icon-button",
                                "#mobileTodayContent .mobile-action-button",
                                "#mobileTodayContent .mobile-row",
                                ".mobile-nav-button",
                            ],
                            f"today-{width}",
                        )

                        # Tasks and task editor.
                        page.locator('#mobileBottomNav [data-view="tasks"]').click()
                        page.wait_for_function(
                            "document.getElementById('app').classList.contains('library-active') && "
                            "document.querySelector('#libraryList .planner-task-toolbar')"
                        )
                        assert_no_horizontal_overflow(page, f"tasks-{width}")
                        assert_touch_targets(
                            page,
                            [
                                "#plannerTaskHeaderActions button",
                                "#libraryList .planner-task-filter",
                                "#libraryList .planner-task-action",
                            ],
                            f"tasks-{width}",
                        )
                        task_card = page.locator("#libraryList .planner-task-card").first
                        expect(task_card).to_be_visible()
                        task_card.click()
                        expect(page.locator("#taskEditorBackdrop")).to_have_class(
                            __import__("re").compile(r"\bopen\b")
                        )
                        assert_no_horizontal_overflow(page, f"task-editor-{width}")
                        assert_touch_targets(
                            page,
                            [
                                "#taskEditorBackdrop .task-editor-close",
                                "#taskEditorBackdrop .task-editor-button",
                                "#taskEditorBackdrop input",
                                "#taskEditorBackdrop select",
                            ],
                            f"task-editor-{width}",
                        )
                        page.locator("#taskEditorBackdrop .task-editor-close").click()

                        # Notes and note window/editor.
                        page.locator("#libraryNotesTab").click()
                        page.wait_for_function(
                            "document.querySelector('#libraryNotesTab')?.getAttribute('aria-selected') === 'true'"
                        )
                        page.wait_for_timeout(150)
                        assert_no_horizontal_overflow(page, f"notes-{width}")
                        assert_touch_targets(
                            page,
                            ["#notesHeaderActions button"],
                            f"notes-{width}",
                        )
                        note_card = page.locator("#libraryList .library-card[data-type='note']").first
                        expect(note_card).to_be_visible()
                        note_card.click()
                        expect(page.locator("#noteWindowBackdrop")).to_have_class(
                            __import__("re").compile(r"\bopen\b")
                        )
                        assert_no_horizontal_overflow(page, f"note-window-{width}")
                        assert_touch_targets(
                            page,
                            [
                                "#noteWindowBackdrop .note-window-close",
                                "#noteWindowBackdrop .note-window-button",
                            ],
                            f"note-window-{width}",
                        )

                        assert not errors, f"page errors: {errors}"
                        page.screenshot(
                            path=str(args.output / f"layout-{width}.png"),
                            full_page=True,
                        )
                        results.append({"viewport": viewport, "status": "passed"})
                    except Exception as exc:
                        page.screenshot(
                            path=str(args.output / f"layout-{viewport['width']}-failed.png"),
                            full_page=True,
                        )
                        results.append({
                            "viewport": viewport,
                            "status": "failed",
                            "error": str(exc),
                            "page_errors": errors,
                        })
                    finally:
                        context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        temporary.cleanup()

    (args.output / "results.json").write_text(
        __import__("json").dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failed = [row for row in results if row["status"] != "passed"]
    print(__import__("json").dumps(results, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
