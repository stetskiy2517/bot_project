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
os.environ["BASE_URL"] = ""
os.environ["WEB_SESSION_SECRET"] = secrets.token_hex(32)


def main() -> None:
    temporary = tempfile.TemporaryDirectory()
    os.environ["DB_PATH"] = str(Path(temporary.name) / "file-ingest-browser.db")

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
        user_id = get_or_create_google_user(label, label + "@example.test", "File Test")
        save_user_timezone(user_id, "Europe/Moscow")
        session_value = app.session_interface.get_signing_serializer(app).dumps({
            "user_id": user_id,
            "auth_time": time.time(),
            "csrf_token": secrets.token_urlsafe(32),
            "_permanent": True,
        })

        proposal = {
            "ok": True,
            "document_type": "flight_ticket",
            "summary": "Нашёл авиабилет Таллин — Хельсинки.",
            "warnings": [],
            "events": [{
                "title": "Рейс AY101 Таллин — Хельсинки",
                "start": "2026-10-01T10:00:00+03:00",
                "end": "2026-10-01T11:30:00+03:00",
                "start_timezone": "Europe/Tallinn",
                "end_timezone": "Europe/Helsinki",
                "location": "TLL → HEL",
                "description": "Рейс AY101",
                "category": "travel",
                "confidence": 0.96,
                "ready": True,
                "warnings": [],
            }],
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
                "**/api/files/analyze",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(proposal, ensure_ascii=False),
                ),
            )
            page.route(
                "**/api/files/calendar",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "event": {
                            "id": "event-1",
                            "title": "Рейс AY101 Таллин — Хельсинки",
                            "start": "2026-10-01T10:00:00+03:00",
                            "end": "2026-10-01T11:30:00+03:00",
                        },
                    }, ensure_ascii=False),
                ),
            )

            page.goto(base)
            page.wait_for_function(
                "window.PlannerRequests && !document.getElementById('login').classList.contains('open')"
            )
            page.wait_for_function("document.getElementById('app').classList.contains('mobile-shell')")
            page.wait_for_function("document.getElementById('app').classList.contains('mobile-view-home')")

            # Voice home contains only the main hold-to-record control.
            expect(page.locator("#voiceBtn")).to_be_visible()
            expect(page.locator("#mobileBottomNav")).to_be_visible()
            expect(page.locator(".composer-wrap")).not_to_be_visible()
            expect(page.locator("#message")).not_to_be_visible()
            expect(page.locator("#fileAttachBtn")).not_to_be_visible()

            # The late file-ingest style must not be able to force the composer onto home.
            expect(page.locator("#fileIngestStyles")).to_have_count(1)
            expect(page.locator(".composer-wrap")).not_to_be_visible()

            # Composer belongs to the dedicated chat tab and must sit above bottom navigation.
            page.locator('#mobileBottomNav [data-view="chat"]').click()
            page.wait_for_function("document.getElementById('app').classList.contains('mobile-view-chat')")
            expect(page.locator(".composer-wrap")).to_be_visible()
            expect(page.locator("#message")).to_be_visible()
            expect(page.locator("#fileAttachBtn")).to_be_visible()
            expect(page.locator("#chatVoiceBtn")).to_be_visible()

            composer_box = page.locator(".composer-wrap").bounding_box()
            nav_box = page.locator("#mobileBottomNav").bounding_box()
            assert composer_box and nav_box
            composer_bottom = composer_box["y"] + composer_box["height"]
            assert composer_bottom <= nav_box["y"] + 1, (composer_box, nav_box)

            page.locator("#message").fill("встреча завтра в 15")
            expect(page.locator("#message")).to_have_value("встреча завтра в 15")
            page.locator("#message").fill("")

            page.locator("#fileAttachInput").set_input_files({
                "name": "ticket.pdf",
                "mimeType": "application/pdf",
                "buffer": b"%PDF-1.4 test ticket",
            })
            card = page.locator(".file-analysis-card")
            expect(card).to_be_visible()
            expect(card).to_contain_text("AY101")
            add_button = card.locator("button")
            expect(add_button).to_have_text("Добавить в календарь")
            expect(add_button).to_be_enabled()
            add_button.click()
            expect(add_button).to_have_text("Добавлено ✓")

            # Returning to voice mode hides the composer again.
            page.locator('#mobileBottomNav [data-view="home"]').click()
            page.wait_for_function("document.getElementById('app').classList.contains('mobile-view-home')")
            expect(page.locator("#voiceBtn")).to_be_visible()
            expect(page.locator(".composer-wrap")).not_to_be_visible()

            assert not errors, errors
            context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        temporary.cleanup()


if __name__ == "__main__":
    main()
