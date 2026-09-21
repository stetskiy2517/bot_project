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

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

VIEWPORTS = [
    {"width": 320, "height": 568},
    {"width": 360, "height": 800},
    {"width": 390, "height": 844},
    {"width": 430, "height": 932},
]

AUDIT_JS = r"""
() => {
  const visible = el => {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  const path = el => {
    if (el.id) return '#' + el.id;
    const cls = [...el.classList].slice(0,3).join('.');
    return el.tagName.toLowerCase() + (cls ? '.' + cls : '');
  };
  const findings = [];
  const intersectsViewport = r => r.right > 0 && r.left < innerWidth && r.bottom > 0 && r.top < innerHeight;
  const controls = [...document.querySelectorAll('button,a,input,select,textarea,[role="button"],[tabindex]')].filter(visible);
  for (const el of controls) {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    const active = s.pointerEvents !== 'none' && intersectsViewport(r);
    if (active && (r.width < 44 || r.height < 44) && !el.matches('input[type="checkbox"],input[type="radio"]')) {
      findings.push({kind:'small-target', selector:path(el), width:+r.width.toFixed(1), height:+r.height.toFixed(1), text:(el.getAttribute('aria-label')||el.textContent||'').trim().slice(0,80)});
    }
    if (intersectsViewport(r) && +s.opacity < 0.1 && s.pointerEvents !== 'none') {
      findings.push({kind:'invisible-clickable', selector:path(el), opacity:s.opacity, text:(el.getAttribute('aria-label')||el.textContent||'').trim().slice(0,80)});
    }
    if (active && (r.left < -1 || r.right > innerWidth + 1)) {
      findings.push({kind:'control-clipped', selector:path(el), left:+r.left.toFixed(1), right:+r.right.toFixed(1), viewport:innerWidth});
    }
  }
  for (const el of [...document.querySelectorAll('body *')].filter(visible)) {
    const r = el.getBoundingClientRect();
    if (!intersectsViewport(r)) continue;
    const s = getComputedStyle(el);
    const size = parseFloat(s.fontSize);
    if (size && size < 11 && (el.textContent||'').trim() && el.children.length === 0) {
      findings.push({kind:'tiny-text', selector:path(el), fontSize:size, text:(el.textContent||'').trim().slice(0,80)});
    }
  }
  if (document.documentElement.scrollWidth > innerWidth + 2) {
    findings.push({kind:'page-horizontal-overflow', selector:'html', clientWidth:innerWidth, scrollWidth:document.documentElement.scrollWidth});
  }
  return findings;
}
"""

def main():
    temporary = tempfile.TemporaryDirectory()
    os.environ["DB_PATH"] = str(Path(temporary.name) / "design-hunt.db")

    import web_app
    from core.db import get_or_create_google_user, save_user_timezone
    from core.note_store import create_note
    from core.reminder_store import create_reminder
    from core.task_planner_store import create_planner_task
    from datetime import datetime, timedelta, timezone

    app = web_app.create_web_app()
    user_id = get_or_create_google_user("design-hunt", "design@example.test", "Design Hunt")
    save_user_timezone(user_id, "Europe/Moscow")
    for i in range(8):
        create_note(user_id, "Очень длинный текст заметки " * 8 + str(i), title=("Очень длинное название заметки " * 3) + str(i))
        create_planner_task(
            user_id,
            ("Очень длинная задача с большим количеством слов " * 3) + str(i),
            description="Описание задачи " * 12,
            due_at=(datetime.now(timezone.utc)+timedelta(days=i+1)).isoformat(),
            priority=("high","normal","low")[i%3],
            category=("work","family","health","personal")[i%4],
            estimate_minutes=30,
            flexible=True,
        )
        create_reminder(user_id, "Напоминание с длинным названием " * 3 + str(i), datetime.now(timezone.utc)+timedelta(days=i+1))
    session_value = app.session_interface.get_signing_serializer(app).dumps({
        "user_id": user_id,
        "auth_time": time.time(),
        "csrf_token": secrets.token_urlsafe(32),
        "_permanent": True,
    })

    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = Path("artifacts/design-hunt")
    output.mkdir(parents=True, exist_ok=True)
    all_rows = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM_PATH") or None, args=["--no-sandbox"])
            for viewport in VIEWPORTS:
                context = browser.new_context(viewport=viewport, has_touch=True, is_mobile=True)
                context.add_cookies([{"name":"session","value":session_value,"url":base,"httpOnly":True,"sameSite":"Lax"}])
                page = context.new_page()
                page.goto(base)
                page.wait_for_function("window.PlannerRequests && !document.getElementById('login').classList.contains('open')")
                page.wait_for_timeout(500)

                states = [("home", None), ("today", '#mobileBottomNav [data-view="today"]'), ("chat", '#mobileBottomNav [data-view="chat"]'), ("tasks", '#mobileBottomNav [data-view="tasks"]')]
                for state, selector in states:
                    if selector:
                        page.locator(selector).click()
                        page.wait_for_timeout(500)
                    rows = page.evaluate(AUDIT_JS)
                    for row in rows:
                        row.update({"viewport": viewport["width"], "state": state})
                        all_rows.append(row)
                    page.screenshot(path=str(output / f"{viewport['width']}-{state}.png"), full_page=True)

                # Open task editor from Tasks.
                if page.locator(".planner-task-card").count():
                    page.locator(".planner-task-card").first.click()
                    page.wait_for_timeout(250)
                    for row in page.evaluate(AUDIT_JS):
                        row.update({"viewport":viewport["width"],"state":"task-editor"})
                        all_rows.append(row)
                    page.keyboard.press("Escape")

                # Notes tab and first note.
                notes = page.locator("#libraryNotesTab")
                if notes.count():
                    notes.click()
                    page.wait_for_timeout(350)
                    for row in page.evaluate(AUDIT_JS):
                        row.update({"viewport":viewport["width"],"state":"notes"})
                        all_rows.append(row)
                    if page.locator(".library-card[data-type='note']").count():
                        page.locator(".library-card[data-type='note']").first.click()
                        page.wait_for_timeout(250)
                        for row in page.evaluate(AUDIT_JS):
                            row.update({"viewport":viewport["width"],"state":"note-detail"})
                            all_rows.append(row)
                        page.keyboard.press("Escape")

                # Settings. Return from the library first so the account
                # control is actually in the active viewport.
                if page.locator("#app").evaluate("el => el.classList.contains('library-active')"):
                    page.locator("#libraryBackBtn").click()
                    page.wait_for_timeout(200)
                page.locator("#accountBtn").click()
                page.wait_for_timeout(250)
                for row in page.evaluate(AUDIT_JS):
                    row.update({"viewport":viewport["width"],"state":"settings"})
                    all_rows.append(row)
                context.close()
            browser.close()
    finally:
        server.shutdown()
        temporary.cleanup()

    # Deduplicate by issue/selector/state, retain narrowest viewport.
    unique = {}
    for row in all_rows:
        key=(row["kind"],row["selector"],row["state"])
        if key not in unique or row["viewport"] < unique[key]["viewport"]:
            unique[key]=row
    rows=sorted(unique.values(), key=lambda r:(r["kind"],r["viewport"],r["state"],r["selector"]))
    (output/"findings.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"count":len(rows),"findings":rows[:120]},ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
