#!/usr/bin/env python3
"""Concurrent load test for the Personal Secretary beta.

Runs entirely against the Flask app in-process with a dedicated SQLite DB.
External providers (Google token exchange, speech recognition, AI file analysis)
are replaced with deterministic local stubs. The goal is to exercise session
isolation, route handling and real concurrent SQLite writes.
"""

from __future__ import annotations

import base64
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import statistics
import threading
import time
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import web_app
from core.db import get_or_create_google_user
from core.push_store import list_push_subscriptions
from core.task_planner_store import create_planner_task, list_planner_tasks
import modules.file_ingest_api as file_ingest_api
from tests.web_test_support import web_test_app


OUT = Path("artifacts/load-test")
OUT.mkdir(parents=True, exist_ok=True)

CONCURRENCY_LEVELS = (25, 50, 100, 200)
MIXED_ROUNDS = 2


def percentile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return float(ordered[index])


def stats(values):
    return {
        "count": len(values),
        "avg_ms": round(statistics.mean(values), 2) if values else 0.0,
        "p50_ms": round(percentile(values, 0.50), 2),
        "p95_ms": round(percentile(values, 0.95), 2),
        "p99_ms": round(percentile(values, 0.99), 2),
        "max_ms": round(max(values), 2) if values else 0.0,
    }


def valid_push_keys():
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return {
        "p256dh": base64.urlsafe_b64encode(point).rstrip(b"=").decode(),
        "auth": base64.urlsafe_b64encode(b"0123456789abcdef").rstrip(b"=").decode(),
    }


PUSH_KEYS = valid_push_keys()


class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.latencies = defaultdict(list)
        self.statuses = Counter()
        self.failures = []

    def request(self, label, fn, expected=(200,)):
        start = time.perf_counter()
        response = fn()
        elapsed = (time.perf_counter() - start) * 1000
        with self.lock:
            self.latencies[label].append(elapsed)
            self.statuses[(label, response.status_code)] += 1
        if response.status_code not in expected:
            body = response.get_data(as_text=True)[:500]
            raise AssertionError(f"{label}: HTTP {response.status_code}, expected {expected}, body={body!r}")
        return response

    def fail(self, phase, worker, exc):
        with self.lock:
            self.failures.append({
                "phase": phase,
                "worker": worker,
                "error": f"{type(exc).__name__}: {exc}",
            })


def bind_user(client, user_id):
    with client.session_transaction() as stored:
        stored.clear()
        stored["user_id"] = int(user_id)
        stored["auth_time"] = time.time()


def bind_oauth(client, state):
    with client.session_transaction() as stored:
        stored.clear()
        stored["oauth_binding"] = {
            "digest": hashlib.sha256(state.encode()).hexdigest(),
            "issued_at": time.time(),
        }


async def fake_process_web_message(text, user_id, user_name):
    return SimpleNamespace(
        handled=True,
        replies=[f"ok:{user_id}"],
        choices=[],
    )


def install_local_stubs():
    web_app.transcribe_audio = lambda stream: "напомни проверить нагрузку завтра в 12"
    web_app.process_web_message = fake_process_web_message

    file_ingest_api.has_ai_access = lambda user_id: True

    def fake_analyze(content, *, filename, mimetype, user_timezone):
        return {
            "summary": f"load:{len(content)}",
            "filename": filename,
            "mimetype": mimetype,
            "timezone": user_timezone,
            "event": None,
        }

    file_ingest_api.analyze_file_bytes = fake_analyze


def oauth_phase(app, recorder, concurrency, offset):
    barrier = threading.Barrier(concurrency)
    users = {}
    users_lock = threading.Lock()

    def fake_signin(state, code):
        index = int(state.rsplit("-", 1)[1])
        return get_or_create_google_user(
            f"load-oauth-{offset}-{index}",
            f"load-oauth-{offset}-{index}@example.test",
            f"OAuth Load {offset}-{index}",
        )

    web_app.complete_web_signin = fake_signin

    def worker(index):
        client = app.test_client()
        state = f"load-state-{offset}-{index}"
        bind_oauth(client, state)
        barrier.wait(timeout=20)
        callback = recorder.request(
            "oauth_callback",
            lambda: client.get(f"/oauth2callback?state={state}&code=code-{index}"),
            expected=(302,),
        )
        if callback.headers.get("Location") != "/?google=connected":
            raise AssertionError("OAuth callback returned unexpected redirect")
        status = recorder.request("oauth_status", lambda: client.get("/api/status"), expected=(200,))
        payload = status.get_json()
        user_id = int(payload["user"]["id"])
        if payload["user"]["email"] != f"load-oauth-{offset}-{index}@example.test":
            raise AssertionError("OAuth session crossed into another user")
        with users_lock:
            users[index] = user_id
        return user_id

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(worker, i): i for i in range(concurrency)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                future.result()
            except Exception as exc:
                recorder.fail(f"oauth-{concurrency}", i, exc)
    return {
        "concurrency": concurrency,
        "elapsed_s": round(time.perf_counter() - started, 2),
        "users": len(users),
    }


def mixed_phase(app, recorder, concurrency, round_no, offset):
    barrier = threading.Barrier(concurrency)
    phase_name = f"mixed-{concurrency}-r{round_no}"

    # Create accounts before the barrier so the measured phase focuses on API
    # concurrency rather than setup.
    user_ids = [
        get_or_create_google_user(
            f"load-mixed-{offset}-{i}",
            f"load-mixed-{offset}-{i}@example.test",
            f"Mixed Load {offset}-{i}",
        )
        for i in range(concurrency)
    ]

    def worker(index):
        user_id = user_ids[index]
        client = app.test_client()
        bind_user(client, user_id)
        barrier.wait(timeout=20)

        status = recorder.request("status_get", lambda: client.get("/api/status"), expected=(200,))
        if int(status.get_json()["user"]["id"]) != user_id:
            raise AssertionError("Authenticated session returned wrong user")

        recorder.request(
            "settings_write",
            lambda: client.post(
                "/api/settings",
                json={
                    "timezone": "Europe/Moscow" if index % 2 == 0 else "Europe/Samara",
                    "work_start": "09:00",
                    "work_end": "18:00",
                    "work_days": [0, 1, 2, 3, 4],
                    "buffer_minutes": index % 31,
                },
            ),
            expected=(200,),
        )

        due = (datetime.now(timezone.utc) + timedelta(days=7, minutes=index)).isoformat()
        task = recorder.request(
            "task_create",
            lambda: client.post(
                "/api/tasks",
                json={
                    "title": f"Load task {offset}-{index}",
                    "description": phase_name,
                    "due_at": due,
                    "priority": ("high", "normal", "low")[index % 3],
                    "category": ("work", "family", "health", "personal")[index % 4],
                    "estimate_minutes": 30,
                    "flexible": True,
                },
            ),
            expected=(201,),
        ).get_json()["task"]

        note = recorder.request(
            "note_create",
            lambda: client.post(
                "/api/note-tools",
                json={
                    "title": f"Load note {offset}-{index}",
                    "text": f"Concurrent note for {phase_name} worker {index}",
                    "tags": ["load", phase_name],
                    "checklist": [],
                    "category": ("work", "family", "health", "personal")[index % 4],
                },
            ),
            expected=(201,),
        ).get_json()["note"]

        endpoint = f"https://fcm.googleapis.com/fcm/send/load-{offset}-{index}"
        recorder.request(
            "push_subscribe",
            lambda: client.post(
                "/api/push/subscriptions",
                json={"endpoint": endpoint, "keys": PUSH_KEYS},
            ),
            expected=(200,),
        )
        push_status = recorder.request("push_status", lambda: client.get("/api/push/status"), expected=(200,))
        if push_status.get_json()["subscriptions"] != 1:
            raise AssertionError("Push subscription count is not isolated")

        recorder.request(
            "voice_post",
            lambda: client.post(
                "/api/voice",
                data={
                    "audio": (io.BytesIO(b"webm-load-audio" * 256), "sample.webm", "audio/webm"),
                    "duration_ms": "1200",
                },
                content_type="multipart/form-data",
            ),
            expected=(200,),
        )

        file_response = recorder.request(
            "file_analyze",
            lambda: client.post(
                "/api/files/analyze",
                data={
                    "file": (
                        io.BytesIO((f"load file {offset}-{index}\n" * 256).encode()),
                        "load.txt",
                        "text/plain",
                    ),
                },
                content_type="multipart/form-data",
            ),
            expected=(200,),
        )
        if not file_response.get_json().get("ok"):
            raise AssertionError("File analysis stub did not return ok")

        library = recorder.request("library_get", lambda: client.get("/api/library"), expected=(200,)).get_json()
        if not any(int(item["id"]) == int(note["note_id"]) for item in library["notes"]):
            raise AssertionError("Own note missing from library")

        own_task = recorder.request(
            "task_get",
            lambda: client.get(f"/api/tasks/{int(task['task_id'])}"),
            expected=(200,),
        ).get_json()["task"]
        if int(own_task["user_id"]) != user_id:
            raise AssertionError("Task ownership leak")

        recorder.request(
            "push_unsubscribe",
            lambda: client.delete("/api/push/subscriptions", json={"endpoint": endpoint}),
            expected=(200,),
        )
        if list_push_subscriptions(user_id):
            raise AssertionError("Push subscription was not deleted")

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(worker, i): i for i in range(concurrency)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                future.result()
            except Exception as exc:
                recorder.fail(phase_name, i, exc)

    elapsed = time.perf_counter() - started
    return {
        "concurrency": concurrency,
        "round": round_no,
        "elapsed_s": round(elapsed, 2),
        "sessions_per_second": round(concurrency / elapsed, 2) if elapsed else 0.0,
    }


def unauthorized_phase(app, recorder, concurrency=100):
    barrier = threading.Barrier(concurrency)

    def worker(index):
        client = app.test_client()
        barrier.wait(timeout=20)
        for path in ("/api/status", "/api/tasks", "/api/library", "/api/push/status"):
            recorder.request(
                "unauthorized_reject",
                lambda path=path: client.get(path),
                expected=(401,),
            )

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(worker, i): i for i in range(concurrency)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                future.result()
            except Exception as exc:
                recorder.fail("unauthorized", i, exc)


def recurring_completion_race(app, recorder, concurrency=50):
    user_id = get_or_create_google_user(
        "load-race-recurring",
        "load-race-recurring@example.test",
        "Recurring Race",
    )
    due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    task = create_planner_task(
        user_id,
        "Recurring race task",
        due_at=due,
        priority="normal",
        category="work",
        estimate_minutes=15,
        flexible=True,
        repeat_rule="daily",
    )
    task_id = int(task["task_id"])
    barrier = threading.Barrier(concurrency)
    returned_next_ids = []
    race_statuses = Counter()
    ids_lock = threading.Lock()

    def worker(index):
        client = app.test_client()
        bind_user(client, user_id)
        barrier.wait(timeout=20)
        response = recorder.request(
            "recurring_complete_race",
            lambda: client.patch(f"/api/tasks/{task_id}", json={"status": "done"}),
            expected=(200, 409),
        )
        with ids_lock:
            race_statuses[response.status_code] += 1
        if response.status_code == 409:
            payload = response.get_json() or {}
            if payload.get("error") != "user_busy":
                raise AssertionError(f"Unexpected 409 during recurring race: {payload!r}")
            return
        next_task = response.get_json().get("next_task")
        if next_task:
            with ids_lock:
                returned_next_ids.append(int(next_task["task_id"]))

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(worker, i): i for i in range(concurrency)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                future.result()
            except Exception as exc:
                recorder.fail("recurring-race", i, exc)

    tasks = list_planner_tasks(user_id, status=None, limit=500)
    generated = [
        item for item in tasks
        if int(item["task_id"]) != task_id
        and item.get("title") == "Recurring race task"
        and item.get("repeat_rule") == "daily"
    ]
    return {
        "concurrency": concurrency,
        "http_200": int(race_statuses[200]),
        "http_409_user_busy": int(race_statuses[409]),
        "returned_next_ids": returned_next_ids,
        "generated_next_tasks": [int(item["task_id"]) for item in generated],
        "ok": (
            len(generated) == 1
            and len(set(returned_next_ids)) == 1
            and race_statuses[200] == 1
            and race_statuses[409] == concurrency - 1
        ),
    }


def push_ownership_race(app, recorder):
    user_a = get_or_create_google_user("load-push-a", "load-push-a@example.test", "Push A")
    user_b = get_or_create_google_user("load-push-b", "load-push-b@example.test", "Push B")
    endpoint = "https://fcm.googleapis.com/fcm/send/shared-load-endpoint"
    barrier = threading.Barrier(2)
    results = []
    lock = threading.Lock()

    def worker(user_id):
        client = app.test_client()
        bind_user(client, user_id)
        barrier.wait(timeout=10)
        start = time.perf_counter()
        response = client.post("/api/push/subscriptions", json={"endpoint": endpoint, "keys": PUSH_KEYS})
        elapsed = (time.perf_counter() - start) * 1000
        with recorder.lock:
            recorder.latencies["push_ownership_race"].append(elapsed)
            recorder.statuses[("push_ownership_race", response.status_code)] += 1
        with lock:
            results.append((user_id, response.status_code))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, user_a), pool.submit(worker, user_b)]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                recorder.fail("push-ownership-race", -1, exc)

    winners = [user_id for user_id, status in results if status == 200]
    losers = [user_id for user_id, status in results if status == 400]
    owner_a = bool(list_push_subscriptions(user_a))
    owner_b = bool(list_push_subscriptions(user_b))
    return {
        "results": results,
        "winner_count": len(winners),
        "loser_count": len(losers),
        "stored_for_a": owner_a,
        "stored_for_b": owner_b,
        "ok": len(winners) == 1 and len(losers) == 1 and (owner_a ^ owner_b),
    }


def main():
    install_local_stubs()
    app = web_test_app()
    app.config.update(TESTING=False)
    recorder = Recorder()

    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "concurrency_levels": list(CONCURRENCY_LEVELS),
        "oauth": [],
        "mixed": [],
    }

    unauthorized_phase(app, recorder, concurrency=100)

    offset = 0
    for concurrency in CONCURRENCY_LEVELS:
        report["oauth"].append(oauth_phase(app, recorder, concurrency, offset))
        offset += concurrency

    mixed_offset = 10000
    for round_no in range(1, MIXED_ROUNDS + 1):
        for concurrency in CONCURRENCY_LEVELS:
            report["mixed"].append(
                mixed_phase(app, recorder, concurrency, round_no, mixed_offset)
            )
            mixed_offset += concurrency

    report["recurring_completion_race"] = recurring_completion_race(app, recorder, 50)
    report["push_ownership_race"] = push_ownership_race(app, recorder)

    report["latencies"] = {
        label: stats(values)
        for label, values in sorted(recorder.latencies.items())
    }
    report["statuses"] = {
        f"{label}:HTTP{status}": count
        for (label, status), count in sorted(recorder.statuses.items())
    }
    report["failures"] = recorder.failures
    report["failed_requests_or_sessions"] = len(recorder.failures)

    # Race failures are important even if every HTTP request returned success.
    report["critical_races_ok"] = bool(
        report["recurring_completion_race"]["ok"]
        and report["push_ownership_race"]["ok"]
    )
    report["ok"] = not recorder.failures and report["critical_races_ok"]

    (OUT / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# Concurrent load test",
        "",
        f"- Result: **{'PASS' if report['ok'] else 'FAIL'}**",
        f"- HTTP/session failures: **{len(recorder.failures)}**",
        f"- Recurring completion race: **{'PASS' if report['recurring_completion_race']['ok'] else 'FAIL'}**",
        f"- Push ownership race: **{'PASS' if report['push_ownership_race']['ok'] else 'FAIL'}**",
        "",
        "## Mixed load",
        "",
        "| Concurrency | Round | Elapsed s | Sessions/s |",
        "|---:|---:|---:|---:|",
    ]
    for row in report["mixed"]:
        lines.append(
            f"| {row['concurrency']} | {row['round']} | {row['elapsed_s']} | {row['sessions_per_second']} |"
        )
    lines += [
        "",
        "## Endpoint latency",
        "",
        "| Operation | Count | Avg ms | P95 ms | P99 ms | Max ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, row in report["latencies"].items():
        lines.append(
            f"| {label} | {row['count']} | {row['avg_ms']} | {row['p95_ms']} | {row['p99_ms']} | {row['max_ms']} |"
        )
    if recorder.failures:
        lines += ["", "## Failure samples", ""]
        for failure in recorder.failures[:50]:
            lines.append(f"- {failure}")

    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
