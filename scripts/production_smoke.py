"""Side-effect-free production diagnostics for the deployed server.

The script is designed for scheduled SSH health checks. It does not call paid or
user-facing external APIs and never prints credentials or user data.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def _database_status() -> dict:
    from core.db import conn, db_lock

    with db_lock:
        row = conn.execute("SELECT 1").fetchone()
    if not row or int(row[0]) != 1:
        raise RuntimeError("database probe returned an unexpected result")
    return {"status": "ok"}


def _ai_status() -> dict:
    from integrations.ai import get_ai_status

    status = get_ai_status()
    allowed = {
        "enabled",
        "provider",
        "configured",
        "model",
        "state",
        "last_error",
        "retry_after_seconds",
        "updated_at",
    }
    return {key: status.get(key) for key in allowed if key in status}


def _navigation_status() -> dict:
    from modules.navigation import navigation_configured, navigation_provider

    return {
        "provider": navigation_provider(),
        "configured": bool(navigation_configured()),
    }


def _disk_status() -> dict:
    usage = shutil.disk_usage(PROJECT_ROOT)
    percent = round((usage.used / usage.total) * 100, 1) if usage.total else 0.0
    return {
        "used_percent": percent,
        "free_mb": round(usage.free / (1024 * 1024)),
    }


def build_report(
    expected_sha: str | None = None,
    max_disk_percent: float = 90.0,
    warn_disk_percent: float = 80.0,
) -> tuple[dict, list[str]]:
    failures: list[str] = []
    actual_sha = _git_sha()
    report = {
        "status": "ok",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "sha": actual_sha,
    }

    expected = (expected_sha or "").strip()
    if expected and actual_sha != expected:
        failures.append(f"deployed SHA {actual_sha} does not match expected {expected}")

    try:
        report["database"] = _database_status()
    except Exception as exc:
        report["database"] = {"status": "error", "error": type(exc).__name__}
        failures.append("database probe failed")

    try:
        report["ai"] = _ai_status()
    except Exception as exc:
        report["ai"] = {"state": "unknown", "error": type(exc).__name__}

    try:
        report["navigation"] = _navigation_status()
    except Exception as exc:
        report["navigation"] = {"configured": False, "error": type(exc).__name__}

    disk = _disk_status()
    report["disk"] = disk
    used_percent = float(disk["used_percent"])
    if used_percent >= max_disk_percent:
        failures.append(f"disk usage is {disk['used_percent']}%")
    elif used_percent >= warn_disk_percent:
        report["warnings"] = [f"disk usage is {disk['used_percent']}%"]

    if failures:
        report["status"] = "error"
        report["failures"] = failures
    return report, failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--max-disk-percent", type=float, default=90.0)
    parser.add_argument("--warn-disk-percent", type=float, default=80.0)
    args = parser.parse_args()

    report, failures = build_report(
        args.expected_sha,
        args.max_disk_percent,
        args.warn_disk_percent,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
