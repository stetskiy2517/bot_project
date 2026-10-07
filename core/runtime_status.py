"""Status and release identity captured by the running application process."""
import hashlib
import hmac
from pathlib import Path
import subprocess
import time

from config import WEB_SESSION_SECRET

STARTED_AT = time.time()
try:
    RUNNING_SHA = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], stderr=subprocess.DEVNULL, text=True).strip()
except (OSError, subprocess.SubprocessError):
    RUNNING_SHA = "unknown"


def monitor_token():
    return hmac.new(WEB_SESSION_SECRET.encode(), b"internal-runtime-monitor-v1", hashlib.sha256).hexdigest()


def runtime_report():
    from integrations.ai import get_ai_status
    from core.db import conn, db_lock
    with db_lock:
        conn.execute("SELECT 1").fetchone()
    return {"status": "ok", "sha": RUNNING_SHA, "started_at": STARTED_AT,
            "checked_at": time.time(), "ai": get_ai_status(), "database": {"status": "ok"}}
