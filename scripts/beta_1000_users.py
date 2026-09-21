#!/usr/bin/env python3
"""Deterministic beta simulation for 1,000 distinct virtual users.

The run stays inside the GitHub Actions runner: Flask test clients, SQLite and
pure planner logic only. It never creates real external accounts and never
calls Google, GigaChat, speech or routing providers.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics
import time
from zoneinfo import ZoneInfo

from core.db import get_or_create_google_user
from core.note_store import search_notes
from core.reminder_store import create_reminder, list_active_reminders
from core.task_planner_store import get_planner_task, list_planner_tasks
from modules.language_support import canonicalize_english, detect_input_language
from modules.router_core import (
    INTENT_CREATE,
    INTENT_DELETE,
    INTENT_FREE,
    INTENT_UPDATE,
    INTENT_VIEW,
    detect_intent,
)
from tests.web_test_support import web_test_app


SEED = 20260921
USER_COUNT = 1000
OUT_DIR = Path("artifacts/beta-1000-users")
SUMMARY_JSON = OUT_DIR / "summary.json"
SUMMARY_MD = OUT_DIR / "summary.md"

CATEGORIES = ("work", "health", "rest", "travel", "family", "personal", "other")
PRIORITIES = ("high", "normal", "low")
REPEAT_RULES = (None, "daily", "weekdays", "weekends", "weekly", "weekly:0", "weekly:4")
COHORTS = (
    "ru_standard",
    "ru_typos",
    "english",
    "moscow_saratov",
    "long_haul",
    "dst_travel",
    "recurring",
    "shift_workers",
    "heavy_usage",
    "invalid_and_isolation",
)
TZ_PAIRS = {
    "ru_standard": ("Europe/Moscow", "Europe/Moscow"),
    "ru_typos": ("Europe/Moscow", "Europe/Moscow"),
    "english": ("Europe/London", "Europe/London"),
    "moscow_saratov": ("Europe/Moscow", "Europe/Saratov"),
    "long_haul": ("Europe/Moscow", "Asia/Vladivostok"),
    "dst_travel": ("Europe/Tallinn", "Europe/Berlin"),
    "recurring": ("Europe/Moscow", "Europe/Samara"),
    "shift_workers": ("Asia/Yekaterinburg", "Asia/Yekaterinburg"),
    "heavy_usage": ("Asia/Novosibirsk", "Asia/Novosibirsk"),
    "invalid_and_isolation": ("Europe/Kaliningrad", "Europe/Moscow"),
}
WORK_PATTERNS = (
    ("09:00", "18:00", [0, 1, 2, 3, 4]),
    ("08:00", "17:00", [0, 1, 2, 3, 4]),
    ("10:00", "19:00", [0, 1, 2, 3, 4]),
    ("07:30", "16:30", [0, 1, 2, 3, 4, 5]),
    ("12:00", "21:00", [1, 2, 3, 4, 5]),
    ("06:00", "14:00", [0, 1, 2, 3, 4, 5, 6]),
)
ENGLISH_CASES = (
    ("Schedule a meeting with John tomorrow at 3 pm", INTENT_CREATE),
    ("When am I free tomorrow?", INTENT_FREE),
    ("Move the meeting with John to Friday at 4 pm", INTENT_UPDATE),
    ("Cancel the meeting with John tomorrow", INTENT_DELETE),
    ("What's on Friday?", INTENT_VIEW),
)
RUSSIAN_CASES = (
    ("поставь врача завтра в 19", INTENT_CREATE),
    ("добавь встречу в пятницу в 10:30", INTENT_CREATE),
    ("что у меня завтра?", INTENT_VIEW),
    ("когда свободен завтра после обеда?", INTENT_FREE),
    ("перенеси врача на субботу", INTENT_UPDATE),
)
TYPO_CASES = (
    ("встеча завтра в 15", INTENT_CREATE),
    ("созовон пятнца в 10", INTENT_CREATE),
    ("удоли встречу завтра", INTENT_DELETE),
    ("перинеси врача на субботу", INTENT_UPDATE),
)


class Metrics:
    def __init__(self) -> None:
        self.checks = 0
        self.requests = 0
        self.operations = Counter()
        self.cohorts = defaultdict(lambda: {"users": 0, "passed": 0, "failed": 0, "durations_ms": []})
        self.failures = []
        self.user_durations_ms = []

    def require(self, condition: bool, message: str) -> None:
        self.checks += 1
        if not condition:
            raise AssertionError(message)

    def response(self, response, expected: int, label: str):
        self.requests += 1
        self.operations[label] += 1
        self.require(
            response.status_code == expected,
            f"{label}: expected HTTP {expected}, got {response.status_code}, body={response.get_data(as_text=True)[:500]!r}",
        )
        return response


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return float(ordered[index])


def utc_iso_for_local(timezone_name: str, hour: int = 23, minute: int = 0) -> str:
    # A fixed future date makes the run reproducible while still exercising
    # IANA timezone conversion and DST-aware zones.
    local = datetime(2026, 11, 15, hour, minute, tzinfo=ZoneInfo(timezone_name))
    return local.astimezone(timezone.utc).isoformat()


def local_clock(value: str, timezone_name: str) -> tuple[int, int]:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(ZoneInfo(timezone_name))
    return local.hour, local.minute


def bind_user(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session.clear()
        session["user_id"] = int(user_id)


def colors_for(index: int) -> dict[str, str | None]:
    ids = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11")
    result = {}
    for offset, category in enumerate(CATEGORIES):
        result[category] = None if category == "other" and index % 7 == 0 else ids[(index + offset) % len(ids)]
    return result


def language_check(metrics: Metrics, cohort: str, index: int) -> None:
    if cohort == "english":
        text, expected = ENGLISH_CASES[index % len(ENGLISH_CASES)]
        metrics.require(detect_input_language(text) == "en", f"english language detection failed: {text}")
        canonical = canonicalize_english(text)
        metrics.require(detect_intent(canonical).name == expected, f"english intent failed: {text} -> {canonical}")
        metrics.operations["english_intent"] += 1
        return
    if cohort == "ru_typos":
        text, expected = TYPO_CASES[index % len(TYPO_CASES)]
        metrics.require(detect_input_language(text) == "ru", f"russian typo language detection failed: {text}")
        metrics.require(detect_intent(text).name == expected, f"typo intent failed: {text}")
        metrics.operations["typo_intent"] += 1
        return
    text, expected = RUSSIAN_CASES[index % len(RUSSIAN_CASES)]
    metrics.require(detect_input_language(text) == "ru", f"russian language detection failed: {text}")
    metrics.require(detect_intent(text).name == expected, f"russian intent failed: {text}")
    metrics.operations["russian_intent"] += 1


def simulate_user(
    app,
    metrics: Metrics,
    index: int,
    previous_task_id: int | None,
    previous_note_id: int | None,
) -> tuple[int, int]:
    cohort = COHORTS[index % len(COHORTS)]
    source_tz, target_tz = TZ_PAIRS[cohort]
    started = time.perf_counter()
    metrics.cohorts[cohort]["users"] += 1

    google_sub = f"beta-1000-{SEED}-{index:04d}"
    email = f"beta{index:04d}@example.test"
    user_id = get_or_create_google_user(google_sub, email, f"Beta User {index:04d}")
    client = app.test_client()
    bind_user(client, user_id)

    work_start, work_end, work_days = WORK_PATTERNS[index % len(WORK_PATTERNS)]
    if cohort == "shift_workers":
        work_start, work_end = ("06:00", "14:00") if index % 2 == 0 else ("14:00", "22:00")
        work_days = [0, 1, 2, 3, 4, 5, 6]

    settings_payload = {
        "timezone": source_tz,
        "work_start": work_start,
        "work_end": work_end,
        "work_days": work_days,
        "buffer_minutes": (index * 5) % 45,
        "category_colors": colors_for(index),
    }
    response = metrics.response(client.post("/api/settings", json=settings_payload), 200, "settings_write")
    status = response.get_json()
    metrics.require(status["timezone"] == source_tz, f"timezone not saved for user {index}")
    metrics.require(status["preferences"]["work_start"] == work_start, f"work start mismatch for user {index}")
    metrics.require(status["preferences"]["work_end"] == work_end, f"work end mismatch for user {index}")
    metrics.require(status["preferences"]["work_days"] == sorted(set(work_days)), f"work days mismatch for user {index}")

    category = CATEGORIES[index % len(CATEGORIES)]
    priority = PRIORITIES[index % len(PRIORITIES)]
    due_at = utc_iso_for_local(source_tz)
    title = (
        f"Подготовить отчёт #{index}"
        if cohort != "english"
        else f"Prepare quarterly report #{index}"
    )
    task_payload = {
        "title": title,
        "description": f"beta simulation cohort={cohort} user={index}",
        "due_at": due_at,
        "priority": priority,
        "category": category,
        "estimate_minutes": 15 + (index % 8) * 15,
        "flexible": bool(index % 2),
    }
    task_response = metrics.response(client.post("/api/tasks", json=task_payload), 201, "task_create")
    task = task_response.get_json()["task"]
    task_id = int(task["task_id"])
    metrics.require(task["user_id"] == user_id, f"task ownership mismatch for user {index}")
    metrics.require(task["priority"] == priority, f"task priority mismatch for user {index}")
    metrics.require(task["category"] == category, f"task category mismatch for user {index}")
    metrics.require(local_clock(task["due_at"], source_tz) == (23, 0), f"task local time mismatch before travel user {index}")

    note_payload = {
        "title": f"Beta note {index}",
        "text": f"Уникальная заметка beta-token-{index:04d} для когорты {cohort}",
        "pinned": index % 11 == 0,
        "tags": ["beta", cohort, f"user-{index:04d}"],
        "checklist": [],
        "category": category,
    }
    note_response = metrics.response(client.post("/api/note-tools", json=note_payload), 201, "note_create")
    note = note_response.get_json()["note"]
    note_id = int(note["note_id"])
    metrics.require(note["category"] == category, f"note category mismatch for user {index}")
    note_hits = search_notes(user_id, f"beta-token-{index:04d}", limit=10)
    metrics.require(any(int(item["note_id"]) == note_id for item in note_hits), f"note search missed user {index}")
    metrics.operations["note_search"] += 1

    repeat_rule = REPEAT_RULES[index % len(REPEAT_RULES)] if cohort in {"recurring", "dst_travel", "heavy_usage"} else None
    reminder = create_reminder(
        user_id,
        f"Принять таблетку beta-{index:04d}",
        datetime.fromisoformat(due_at),
        repeat_rule=repeat_rule,
        repeat_timezone=source_tz if repeat_rule else None,
    )
    metrics.operations["reminder_create"] += 1
    reminder_id = int(reminder["reminder_id"])
    metrics.require(local_clock(reminder["remind_at"], source_tz) == (23, 0), f"reminder local time mismatch before travel user {index}")

    library = metrics.response(client.get("/api/library"), 200, "library_read").get_json()
    metrics.require(any(int(item["id"]) == note_id for item in library["notes"]), f"library missed note user {index}")
    metrics.require(any(int(item["id"]) == reminder_id for item in library["reminders"]), f"library missed reminder user {index}")

    task_list = metrics.response(client.get(f"/api/tasks?status=all&q={index}"), 200, "task_search").get_json()["tasks"]
    metrics.require(any(int(item["task_id"]) == task_id for item in task_list), f"task search missed user {index}")

    language_check(metrics, cohort, index)

    if previous_task_id is not None:
        foreign_task = metrics.response(client.get(f"/api/tasks/{previous_task_id}"), 404, "task_isolation")
        metrics.require(foreign_task.get_json().get("error") == "task_not_found", f"foreign task leaked to user {index}")
    if previous_note_id is not None:
        foreign_note = metrics.response(client.get(f"/api/note-tools/{previous_note_id}"), 404, "note_isolation")
        metrics.require(foreign_note.get_json().get("error") == "note_not_found", f"foreign note leaked to user {index}")

    # Device timezone change is represented by the same API call the web client
    # makes after Intl.DateTimeFormat().resolvedOptions().timeZone changes.
    if target_tz != source_tz:
        travel = metrics.response(client.post("/api/settings", json={"timezone": target_tz}), 200, "timezone_change")
        metrics.require(travel.get_json()["timezone"] == target_tz, f"target timezone not saved user {index}")
        moved_task = get_planner_task(user_id, task_id)
        metrics.require(moved_task is not None, f"task disappeared after timezone change user {index}")
        metrics.require(local_clock(moved_task["due_at"], target_tz) == (23, 0), f"task wall clock drift user {index}")
        moved_reminders = list_active_reminders(user_id, limit=50)
        moved = next((item for item in moved_reminders if int(item["reminder_id"]) == reminder_id), None)
        metrics.require(moved is not None, f"reminder disappeared after timezone change user {index}")
        metrics.require(local_clock(moved["remind_at"], target_tz) == (23, 0), f"reminder wall clock drift user {index}")
        if repeat_rule:
            metrics.require(moved["repeat_timezone"] == target_tz, f"repeat timezone stale user {index}")
            metrics.require(local_clock(moved["next_remind_at"], target_tz) == (23, 0), f"next repeat wall clock drift user {index}")

        if cohort in {"dst_travel", "recurring"} and index % 2 == 0:
            round_trip = metrics.response(client.post("/api/settings", json={"timezone": source_tz}), 200, "timezone_round_trip")
            metrics.require(round_trip.get_json()["timezone"] == source_tz, f"round trip timezone failed user {index}")
            round_task = get_planner_task(user_id, task_id)
            metrics.require(local_clock(round_task["due_at"], source_tz) == (23, 0), f"round trip task drift user {index}")
            round_reminder = next(
                item for item in list_active_reminders(user_id, limit=50)
                if int(item["reminder_id"]) == reminder_id
            )
            metrics.require(local_clock(round_reminder["remind_at"], source_tz) == (23, 0), f"round trip reminder drift user {index}")

    if cohort == "heavy_usage":
        for extra in range(5):
            extra_due = utc_iso_for_local(target_tz if target_tz != source_tz else source_tz, hour=9 + extra)
            extra_response = metrics.response(
                client.post(
                    "/api/tasks",
                    json={
                        "title": f"Heavy task {index}-{extra}",
                        "description": "bulk beta load",
                        "due_at": extra_due,
                        "priority": PRIORITIES[(index + extra) % len(PRIORITIES)],
                        "category": CATEGORIES[(index + extra) % len(CATEGORIES)],
                        "estimate_minutes": 30,
                        "flexible": True,
                    },
                ),
                201,
                "heavy_task_create",
            )
            metrics.require(extra_response.get_json()["task"]["user_id"] == user_id, f"heavy task owner mismatch user {index}")
        for extra in range(2):
            extra_note = metrics.response(
                client.post(
                    "/api/note-tools",
                    json={
                        "title": f"Heavy note {index}-{extra}",
                        "text": f"heavy-note-token-{index}-{extra}",
                        "tags": ["heavy", "beta"],
                        "checklist": [],
                        "category": CATEGORIES[(index + extra) % len(CATEGORIES)],
                    },
                ),
                201,
                "heavy_note_create",
            )
            metrics.require(extra_note.get_json()["note"]["note_id"] > 0, f"heavy note missing id user {index}")
        all_tasks = list_planner_tasks(user_id, status=None, limit=500)
        metrics.require(len(all_tasks) >= 6, f"heavy user task count too low user {index}")

    if cohort == "invalid_and_isolation":
        before = metrics.response(client.get("/api/status"), 200, "status_read").get_json()
        invalid_tz = metrics.response(client.post("/api/settings", json={"timezone": "Mars/Olympus"}), 400, "invalid_timezone")
        metrics.require(invalid_tz.get_json().get("error") == "invalid_settings", f"invalid timezone not rejected user {index}")
        invalid_days = metrics.response(client.post("/api/settings", json={"work_days": []}), 400, "invalid_work_days")
        metrics.require(invalid_days.get_json().get("error") == "invalid_settings", f"empty work days not rejected user {index}")
        after = metrics.response(client.get("/api/status"), 200, "status_read")
        metrics.require(after.get_json()["timezone"] == before["timezone"], f"invalid setting mutated timezone user {index}")

    if index % 4 == 0:
        done = metrics.response(client.patch(f"/api/tasks/{task_id}", json={"status": "done"}), 200, "task_complete").get_json()["task"]
        metrics.require(done["status"] == "done", f"task completion failed user {index}")

    elapsed_ms = (time.perf_counter() - started) * 1000
    metrics.user_durations_ms.append(elapsed_ms)
    metrics.cohorts[cohort]["durations_ms"].append(elapsed_ms)
    metrics.cohorts[cohort]["passed"] += 1
    return task_id, note_id


def build_summary(metrics: Metrics, elapsed_seconds: float) -> dict:
    cohort_summary = {}
    for cohort in COHORTS:
        values = metrics.cohorts[cohort]
        durations = values["durations_ms"]
        cohort_summary[cohort] = {
            "users": values["users"],
            "passed": values["passed"],
            "failed": values["failed"],
            "avg_ms": round(statistics.mean(durations), 2) if durations else 0.0,
            "p95_ms": round(percentile(durations, 0.95), 2),
        }
    return {
        "seed": SEED,
        "users": USER_COUNT,
        "passed_users": USER_COUNT - len(metrics.failures),
        "failed_users": len(metrics.failures),
        "checks": metrics.checks,
        "http_requests": metrics.requests,
        "operations": dict(metrics.operations),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "avg_user_ms": round(statistics.mean(metrics.user_durations_ms), 2) if metrics.user_durations_ms else 0.0,
        "p50_user_ms": round(percentile(metrics.user_durations_ms, 0.50), 2),
        "p95_user_ms": round(percentile(metrics.user_durations_ms, 0.95), 2),
        "p99_user_ms": round(percentile(metrics.user_durations_ms, 0.99), 2),
        "cohorts": cohort_summary,
        "failures": metrics.failures[:50],
    }


def write_report(summary: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Beta simulation: 1,000 virtual users",
        "",
        f"- Seed: `{summary['seed']}`",
        f"- Users: **{summary['users']}**",
        f"- Passed: **{summary['passed_users']}**",
        f"- Failed: **{summary['failed_users']}**",
        f"- Checks: **{summary['checks']}**",
        f"- HTTP requests: **{summary['http_requests']}**",
        f"- Runtime: **{summary['elapsed_seconds']} s**",
        f"- User latency: avg **{summary['avg_user_ms']} ms**, p95 **{summary['p95_user_ms']} ms**, p99 **{summary['p99_user_ms']} ms**",
        "",
        "| Cohort | Users | Passed | Failed | Avg ms | P95 ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for cohort in COHORTS:
        row = summary["cohorts"][cohort]
        lines.append(
            f"| {cohort} | {row['users']} | {row['passed']} | {row['failed']} | {row['avg_ms']} | {row['p95_ms']} |"
        )
    lines.extend(["", "## Operations", ""])
    for name, count in sorted(summary["operations"].items()):
        lines.append(f"- {name}: {count}")
    if summary["failures"]:
        lines.extend(["", "## Failure samples", ""])
        for failure in summary["failures"]:
            lines.append(
                f"- user {failure['user_index']} / {failure['cohort']}: {failure['error']}"
            )
    SUMMARY_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    random.seed(SEED)
    app = web_test_app()
    app.config.update(TESTING=True)
    metrics = Metrics()
    started = time.perf_counter()
    previous_task_id = None
    previous_note_id = None

    for index in range(USER_COUNT):
        cohort = COHORTS[index % len(COHORTS)]
        try:
            previous_task_id, previous_note_id = simulate_user(
                app, metrics, index, previous_task_id, previous_note_id
            )
        except Exception as exc:
            metrics.cohorts[cohort]["failed"] += 1
            metrics.failures.append(
                {
                    "user_index": index,
                    "cohort": cohort,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    elapsed = time.perf_counter() - started
    summary = build_summary(metrics, elapsed)
    write_report(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nReport: {SUMMARY_MD}")
    return 1 if metrics.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
