"""Run isolated Chromium UI scenarios; audio and API responses are synthetic."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

from playwright.sync_api import sync_playwright

MOCK_MEDIA = r"""
window.auditMedia = {mode: 'immediate', pending: [], streams: [], recorders: [], stops: 0};
Object.defineProperty(navigator, 'mediaDevices', {configurable: true, value: {
  getUserMedia() {
    if (auditMedia.mode === 'denied') return Promise.reject(new DOMException('denied', 'NotAllowedError'));
    const track = {active: true, stop() {this.active = false;}};
    const stream = {getTracks: () => [track]};
    auditMedia.streams.push(stream);
    if (auditMedia.mode === 'deferred') return new Promise((resolve) => auditMedia.pending.push(() => resolve(stream)));
    return Promise.resolve(stream);
  }
}});
window.MediaRecorder = class extends EventTarget {
  static isTypeSupported() {return true;}
  constructor(stream, options = {}) {
    super();
    this.stream = stream;
    this.mimeType = options.mimeType || 'audio/webm';
    this.state = 'inactive';
    auditMedia.recorders.push(this);
  }
  start() {this.state = 'recording';}
  stop() {
    this.state = 'inactive';
    setTimeout(() => {
      this.ondataavailable?.({data: new Blob(['synthetic audio'], {type: this.mimeType})});
      this.onstop?.();
      this.dispatchEvent(new Event('stop'));
      auditMedia.stops += 1;
    }, 0);
  }
};
"""

STATUS = {
    "csrf_token": "synthetic-test-token",
    "timezone": "Europe/Moscow",
    "timezone_set": True,
    "google_connected": True,
    "user": {"id": 1, "name": "Audit", "email": "audit@example.test"},
    "preferences": {"work_start": "09:00", "work_end": "18:00", "work_days": [0, 1, 2, 3, 4], "buffer_minutes": 15},
}


def pointer(page, event: str, selector: str = "#voiceBtn", pointer_id: int = 7, y: int = 250):
    page.locator(selector).dispatch_event(event, {
        "pointerId": pointer_id, "pointerType": "touch", "isPrimary": True,
        "button": 0, "clientX": 100, "clientY": y, "bubbles": True,
    })


def begin(page, selector="#voiceBtn", pointer_id=7):
    pointer(page, "pointerdown", selector, pointer_id)
    page.wait_for_function("recorder && recorder.state === 'recording'")
    page.evaluate("voiceRecordingStartedAt = performance.now() - 500")


def voice_requests(requests):
    return [request for request in requests if request["path"] == "/api/voice"]


def run_case(browser, root, output, case, viewport):
    requests = []
    failures = []
    mode = {"voice_status": 200}
    context = browser.new_context(viewport=viewport, service_workers="block")
    page = context.new_page()
    page.set_default_timeout(5000)
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate("() => {" + MOCK_MEDIA + "}")
    html = (root / "web/index.html").read_text(encoding="utf-8")
    html = re.sub(r"<link\b[^>]*>", "", html)
    scripts = "".join(
        "<script>" + (root / "web" / name).read_text(encoding="utf-8") + "</script>"
        for name in ("reminders.js", "library.js", "voice_gesture.js", "assistant.js")
    )
    helper = "<script>" + (root / "web/reliability.js").read_text(encoding="utf-8") + "</script>"
    html = html.replace("    <script>\n", helper + "    <script>\n", 1)
    html = html.replace("</body>", scripts + "</body>")

    def request_binding(_source, path, method, body):
        requests.append({"path": path, "method": method, "data": body})
        code = 200
        if path == "/api/status":
            data = STATUS
        elif path == "/api/chat":
            data = {"handled": True, "replies": ["Audit response"]}
        elif path == "/api/voice":
            code = mode["voice_status"]
            data = {"handled": True, "transcript": "Audit voice", "replies": ["Audit voice response"]} if code == 200 else {"error": "voice_failed", "message": "Audit provider failure"}
        elif path == "/api/assistant":
            data = {"preferences": {}, "templates": [], "undo": None, "reviews": [], "privacy": {"notice": "Test privacy policy", "backup_retention_days": 14}}
        elif path == "/api/library":
            data = {"timezone": "Europe/Moscow", "notes": [{"id": 1, "title": "Audit note", "text": "Audit body", "created_at": "2026-09-13T10:00:00Z"}], "reminders": []}
        elif path == "/api/library/open":
            data = {"type": "note", "id": 1, "label": "Audit note", "chat_text": "Audit body"}
        elif path == "/api/push/status":
            data = {"subscribed": False, "subscriptions": 0, "devices": []}
        elif path == "/api/reminders/due":
            data = {"reminders": []}
        elif path == "/api/settings":
            data = STATUS
        else:
            code, data = 404, {"error": "unexpected_test_endpoint"}
        return {"status": code, "data": data}

    page.expose_binding("auditRequest", request_binding)
    page.evaluate("""() => { window.fetch = async (path, options = {}) => {
      const response = await window.auditRequest(String(path), options.method || 'GET', options.body instanceof FormData ? '<synthetic multipart>' : (options.body || null));
      return new Response(JSON.stringify(response.data), {status: response.status, headers: {'Content-Type': 'application/json'}});
    }; }""")
    try:
        page.set_content(html, wait_until="load")
        page.wait_for_function("document.getElementById('categoryColors').children.length === 7")
        case(page, requests, mode)
        assert not failures, f"Browser errors: {failures}"
        result = {"name": case.__name__, "status": "passed", "viewport": viewport}
    except Exception as error:
        page.screenshot(path=str(output / f"{case.__name__}-{viewport['width']}.png"), full_page=True)
        result = {"name": case.__name__, "status": "failed", "error": str(error), "viewport": viewport, "page_errors": failures}
    finally:
        context.close()
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def startup(page, requests, mode):
    assert page.locator('select[data-category="family"]').input_value() == "4"
    assert page.locator("#chatVoiceBtn").is_enabled()
    assert not page.locator("#login").evaluate("node => node.classList.contains('open')")


def chat_and_back(page, requests, mode):
    text = "встеча завтро в 15"
    page.locator("#message").fill(text)
    page.locator("#composer").evaluate("form => form.requestSubmit()")
    page.wait_for_function("document.querySelector('#chat').textContent.includes('Audit response')")
    payload = next(json.loads(r["data"]) for r in requests if r["path"] == "/api/chat")
    assert payload["message"] == text
    page.locator("#chatCollapseBtn").click()
    assert not page.locator("#app").evaluate("node => node.classList.contains('chat-active')")


def send_voice(page, requests, mode):
    begin(page)
    pointer(page, "pointerup")
    page.wait_for_function("document.querySelector('#chat').textContent.includes('Audit voice response')")
    assert len(voice_requests(requests)) == 1
    assert page.locator("#chatVoiceBtn").is_enabled()
    begin(page, "#chatVoiceBtn", 8)
    pointer(page, "pointerup", "#chatVoiceBtn", 8)
    page.wait_for_function("document.querySelectorAll('#chat .msg.assistant').length === 2")
    assert len(voice_requests(requests)) == 2


def swipe_cancel(page, requests, mode):
    begin(page)
    pointer(page, "pointermove", y=100)
    pointer(page, "pointerup", y=100)
    page.wait_for_function("auditMedia.stops === 1")
    assert not voice_requests(requests)
    assert page.evaluate("auditMedia.streams.every(s => s.getTracks().every(t => !t.active))")


def pointer_cancel(page, requests, mode):
    begin(page, "#chatVoiceBtn")
    pointer(page, "pointercancel", "#chatVoiceBtn")
    page.wait_for_function("auditMedia.stops === 1")
    assert not voice_requests(requests)


def short_voice(page, requests, mode):
    pointer(page, "pointerdown")
    page.wait_for_function("recorder && recorder.state === 'recording'")
    page.evaluate("voiceRecordingStartedAt = performance.now()")
    pointer(page, "pointerup")
    page.wait_for_function("auditMedia.stops === 1")
    assert not voice_requests(requests)


def denied(page, requests, mode):
    page.evaluate("auditMedia.mode = 'denied'")
    pointer(page, "pointerdown")
    page.wait_for_function("!voicePressActive")
    assert not voice_requests(requests)
    assert "доступа" in page.locator("#voiceCaption").inner_text()


def cancelled_permission(page, requests, mode):
    page.evaluate("auditMedia.mode = 'deferred'")
    pointer(page, "pointerdown")
    pointer(page, "pointerup")
    page.evaluate("auditMedia.pending.shift()()")
    page.wait_for_function("auditMedia.streams.every(s => s.getTracks().every(t => !t.active))")
    assert page.evaluate("auditMedia.recorders.length") == 0
    assert not voice_requests(requests)


def overlapping_permission(page, requests, mode):
    page.evaluate("auditMedia.mode = 'deferred'")
    pointer(page, "pointerdown", pointer_id=7)
    pointer(page, "pointerup", pointer_id=7)
    pointer(page, "pointerdown", pointer_id=8)
    page.evaluate("auditMedia.pending.shift()()")
    page.evaluate("auditMedia.pending.shift()()")
    page.wait_for_function("recorder && recorder.state === 'recording'")
    assert page.evaluate("auditMedia.recorders.filter(r => r.state === 'recording').length") == 1, "Two active recorders"
    assert page.evaluate("auditMedia.streams.filter(s => s.getTracks().some(t => t.active)).length") == 1, "Cancelled stream remains active"


def provider_failure(page, requests, mode):
    mode["voice_status"] = 503
    begin(page)
    pointer(page, "pointerup")
    page.wait_for_function("!sendingVoice && auditMedia.stops === 1")
    assert len(voice_requests(requests)) == 1
    assert page.locator("#chatVoiceBtn").is_enabled()
    assert page.locator("#chat .msg.assistant").count() == 1


def safe_chat_rendering(page, requests, mode):
    page.locator("#message").fill('<img src=x onerror="window.auditInjected=true">')
    page.locator("#composer").evaluate("form => form.requestSubmit()")
    page.wait_for_function("document.querySelector('#chat').textContent.includes('Audit response')")
    assert page.locator("#chat img").count() == 0
    assert not page.evaluate("Boolean(window.auditInjected)")


def library_back(page, requests, mode):
    page.evaluate("window.PlannerLibrary.open()")
    page.wait_for_function("document.querySelector('#libraryList').textContent.includes('Audit note')")
    page.locator("#libraryBackBtn").click()
    assert not page.locator("#app").evaluate("node => node.classList.contains('library-active')")


CASES = (
    startup, chat_and_back, send_voice, swipe_cancel, pointer_cancel, short_voice,
    denied, cancelled_permission, overlapping_permission, provider_failure,
    safe_chat_rendering, library_back,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("audit-evidence/browser"))
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    launch = {"headless": True}
    if os.environ.get("CHROMIUM_PATH"):
        launch["executable_path"] = os.environ["CHROMIUM_PATH"]
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch)
        print("BROWSER_VERSION", browser.version)
        try:
            for viewport in ({"width": 1280, "height": 800}, {"width": 390, "height": 844}):
                for case in CASES:
                    results.append(run_case(browser, options.root, options.output, case, viewport))
        finally:
            browser.close()
    (options.output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(result["status"] == "passed" for result in results)
    print(f"BROWSER_AUDIT: {passed}/{len(results)} passed")
    return int(passed != len(results))


if __name__ == "__main__":
    sys.exit(main())
