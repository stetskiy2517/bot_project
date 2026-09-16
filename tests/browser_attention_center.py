from __future__ import annotations

import json
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
    os.environ["DB_PATH"] = str(Path(temporary.name) / "attention-browser.db")

    import web_app
    from core.attention_store import get_attention_item, upsert_attention_item
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
        user_id = get_or_create_google_user(label, label + "@example.test", "Attention Test")
        save_user_timezone(user_id, "Europe/Moscow")
        session_value = app.session_interface.get_signing_serializer(app).dumps({
            "user_id": user_id,
            "auth_time": time.time(),
            "csrf_token": secrets.token_urlsafe(32),
            "_permanent": True,
        })

        first = upsert_attention_item(
            user_id,
            source_type="assistant_notice",
            source_key="notice-1",
            category="assistant",
            priority="high",
            title="Нужно проверить расписание",
            body="Есть важный сигнал от секретаря.",
        )

        today_payload = {
            "date": "2026-09-16",
            "timezone": "Europe/Moscow",
            "calendar_ok": True,
            "events": [],
            "tasks": [],
            "task_summary": {"open": 0, "overdue": 0},
            "review": {"text": "Утро\nСпокойный день."},
            "attention": [],
        }

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

            page.route(
                "**/api/mobile/today",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(today_payload, ensure_ascii=False),
                ),
            )
            page.route(
                "**/api/navigation/next-route",
                lambda route: route.fulfill(
                    status=404,
                    content_type="application/json",
                    body=json.dumps({"error": "none"}),
                ),
            )
            page.route(
                "**/api/email/action",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"ok": True, "created": True, "already_present": False}),
                ),
            )

            page.goto(f"{base}/?view=today&attention={first['attention_id']}")
            page.wait_for_function(
                "window.PlannerRequests && !document.getElementById('login').classList.contains('open')"
            )
            page.wait_for_function("document.getElementById('app').classList.contains('mobile-view-today')")
            card = page.locator(".mobile-attention-card")
            expect(card).to_be_visible()
            expect(card).to_contain_text("Требует внимания")
            expect(card).to_contain_text("Нужно проверить расписание")
            expect(card.locator(".mobile-attention-count")).to_have_text("1")

            card.get_by_role("button", name="Понятно", exact=True).click()
            expect(page.locator(".mobile-attention-card")).to_have_count(0)
            assert get_attention_item(user_id, first["attention_id"])["dismissed_at"] is not None

            email_item = upsert_attention_item(
                user_id,
                source_type="email_action",
                source_key="mail-1",
                category="email",
                priority="high",
                title="Нужно решение по письму: встреча",
                body="Письмо: подтверждение встречи",
                action_type="email_action",
                action={
                    "action_type": "task",
                    "title": "Подтвердить встречу",
                    "due_at": "2026-09-17T12:00:00+03:00",
                    "priority": "normal",
                    "category": "work",
                    "confidence": 0.96,
                    "ready": True,
                },
            )
            page.locator('#mobileBottomNav [data-view="home"]').click()
            page.locator('#mobileBottomNav [data-view="today"]').click()
            card = page.locator(".mobile-attention-card")
            expect(card).to_be_visible()
            expect(card).to_contain_text("Нужно решение по письму")
            card.get_by_role("button", name="Выполнить", exact=True).click()
            expect(page.locator(".mobile-attention-card")).to_have_count(0)
            assert get_attention_item(user_id, email_item["attention_id"])["dismissed_at"] is not None

            page.locator("#accountBtn").click()
            proactive = page.locator("#assistantSettings details.assistant-section", has_text="Проактивный помощник")
            expect(proactive).to_have_count(1)
            proactive.locator("summary").click()
            expect(page.locator("#attentionPushEnabled")).to_be_visible()
            expect(page.get_by_text("Важные push-уведомления", exact=False)).to_be_visible()

            assert not errors, errors
            context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        temporary.cleanup()


if __name__ == "__main__":
    main()
