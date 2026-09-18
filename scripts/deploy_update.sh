#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-personal-secretary}"
BRANCH="${BRANCH:-main}"
TARGET_SHA="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/api/health}"
NAVIGATION_KEY_FILE="${NAVIGATION_KEY_FILE:-}"
AI_KEY_FILE="${AI_KEY_FILE:-}"
APP_PYTHON="${APP_PYTHON:-python3.12}"
PREVIOUS_SHA=""
PREDEPLOY_BACKUP=""
OLD_VENV_BACKUP=""

log() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

cleanup_temp_secrets() {
  if [ -n "$NAVIGATION_KEY_FILE" ]; then
    rm -f -- "$NAVIGATION_KEY_FILE" 2>/dev/null || true
  fi
  if [ -n "$AI_KEY_FILE" ]; then
    rm -f -- "$AI_KEY_FILE" 2>/dev/null || true
  fi
}
trap cleanup_temp_secrets EXIT

cd "$PROJECT_DIR"

command -v git >/dev/null 2>&1 || fail "git is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v sudo >/dev/null 2>&1 || fail "sudo is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

if [ -z "$TARGET_SHA" ]; then
  fail "Target commit SHA is required"
fi

python_runtime_supported() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

venv_matches_app_python() {
  [ -x .venv/bin/python ] || return 1
  command -v "$APP_PYTHON" >/dev/null 2>&1 || return 1
  .venv/bin/python - "$APP_PYTHON" <<'PY'
import subprocess
import sys

target = subprocess.check_output(
    [sys.argv[1], "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
    text=True,
).strip()
current = f"{sys.version_info.major}.{sys.version_info.minor}"
raise SystemExit(0 if current == target else 1)
PY
}

ensure_app_python() {
  if command -v "$APP_PYTHON" >/dev/null 2>&1 &&
     python_runtime_supported "$APP_PYTHON" &&
     "$APP_PYTHON" -c 'import ensurepip, venv' >/dev/null 2>&1; then
    return 0
  fi

  if [ ! -r /etc/os-release ]; then
    fail "$APP_PYTHON is missing and OS release information is unavailable"
  fi

  # shellcheck disable=SC1091
  . /etc/os-release
  if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "22.04" ]; then
    fail "$APP_PYTHON is missing; automatic runtime bootstrap is only supported on Ubuntu 22.04"
  fi

  log "Installing supported application runtime ($APP_PYTHON)"
  sudo -n apt-get update
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y software-properties-common
  if ! grep -Rqs "deadsnakes/ppa" /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
    sudo -n add-apt-repository -y ppa:deadsnakes/ppa
  fi
  sudo -n apt-get update
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y python3.12 python3.12-venv

  command -v "$APP_PYTHON" >/dev/null 2>&1 || fail "$APP_PYTHON installation did not provide an executable"
  python_runtime_supported "$APP_PYTHON" || fail "$APP_PYTHON is below Python 3.11"
}

rebuild_runtime_venv() {
  log "Rebuilding application virtual environment with $APP_PYTHON"
  rm -rf .venv.next .venv.previous-runtime
  "$APP_PYTHON" -m venv .venv.next
  .venv.next/bin/python -m pip install --upgrade pip setuptools wheel
  .venv.next/bin/python -m pip install -r requirements.txt
  .venv.next/bin/python -c 'import sys; assert sys.version_info >= (3, 11)'

  if [ -d .venv ]; then
    mv .venv .venv.previous-runtime
    OLD_VENV_BACKUP="$PROJECT_DIR/.venv.previous-runtime"
  fi
  mv .venv.next .venv
}

navigation_secret_present() {
  [ -n "$NAVIGATION_KEY_FILE" ] && [ -s "$NAVIGATION_KEY_FILE" ]
}

ai_secret_present() {
  [ -n "$AI_KEY_FILE" ] && [ -s "$AI_KEY_FILE" ]
}

sync_navigation_config() {
  log "Updating navigation configuration"
  python3 - "$PROJECT_DIR/.env" "$NAVIGATION_KEY_FILE" <<'PY'
from pathlib import Path
import os
import sys

env_path = Path(sys.argv[1])
key_path = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else None
updates = {"NAVIGATION_PROVIDER": "ors"}
if key_path and key_path.is_file() and key_path.stat().st_size:
    key = key_path.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("openrouteservice key file is empty")
    updates["ORS_API_KEY"] = key

lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
seen = set()
out = []
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        name = line.split("=", 1)[0].strip()
        if name in updates:
            out.append(f"{name}={updates[name]}")
            seen.add(name)
            continue
    out.append(line)
for name, value in updates.items():
    if name not in seen:
        out.append(f"{name}={value}")
env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
os.chmod(env_path, 0o600)
PY
}

sync_ai_config() {
  log "Updating AI configuration"
  python3 - "$PROJECT_DIR/.env" "$AI_KEY_FILE" <<'PY'
from pathlib import Path
import os
import sys

env_path = Path(sys.argv[1])
key_path = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else None
updates = {
    "AI_ENABLED": "1",
    "AI_PROVIDER": "gigachat",
    "GIGACHAT_SCOPE": "GIGACHAT_API_PERS",
    "GIGACHAT_MODEL": "GigaChat-2",
}
if key_path and key_path.is_file() and key_path.stat().st_size:
    key = key_path.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("GigaChat credential file is empty")
    updates["GIGACHAT_CREDENTIALS"] = key

lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
seen = set()
out = []
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        name = line.split("=", 1)[0].strip()
        if name in updates:
            out.append(f"{name}={updates[name]}")
            seen.add(name)
            continue
    out.append(line)
for name, value in updates.items():
    if name not in seen:
        out.append(f"{name}={value}")
env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
os.chmod(env_path, 0o600)
PY
}

report_ai_status() {
  log "Checking AI configuration"
  .venv/bin/python - <<'PY'
from integrations.ai import complete, get_ai_status

status = get_ai_status()
print(
    "AI status: "
    f"enabled={str(bool(status.get('enabled'))).lower()} "
    f"configured={str(bool(status.get('configured'))).lower()} "
    f"provider={status.get('provider')} model={status.get('model')}"
)
if not status.get("configured"):
    print("AI provider check: skipped because credentials are not configured")
    raise SystemExit(0)
try:
    answer = complete(
        [{"role": "user", "content": "Ответь только словом OK"}],
        max_tokens=8,
        temperature=0.001,
    )
except Exception as exc:
    print(f"AI provider check: FAILED ({type(exc).__name__}: {exc})")
else:
    print("AI provider check: OK" if answer.strip() else "AI provider check: FAILED (empty response)")
PY
}

wait_for_health() {
  local attempts="${1:-30}"
  for _ in $(seq 1 "$attempts"); do
    if curl -fsS --connect-timeout 2 --max-time 4 "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

log "Fetching $BRANCH from GitHub"
git fetch --prune origin "$BRANCH"
git cat-file -e "${TARGET_SHA}^{commit}" 2>/dev/null || fail "Target commit $TARGET_SHA is not available"
git merge-base --is-ancestor "$TARGET_SHA" "origin/$BRANCH" \
  || fail "Target commit $TARGET_SHA is not part of origin/$BRANCH"

PREVIOUS_SHA="$(git rev-parse HEAD)"
if [ "$PREVIOUS_SHA" = "$TARGET_SHA" ] && venv_matches_app_python; then
  if navigation_secret_present || ai_secret_present; then
    sync_navigation_config
    sync_ai_config
    log "Restarting $SERVICE_NAME after secret update"
    sudo -n systemctl restart "$SERVICE_NAME"
    wait_for_health 30 || fail "Application health-check failed after secret update"
    report_ai_status
    log "Secrets applied to already deployed commit $TARGET_SHA"
  else
    log "Commit $TARGET_SHA is already deployed"
  fi
  exit 0
fi

if [ "$PREVIOUS_SHA" != "$TARGET_SHA" ] && git merge-base --is-ancestor "$TARGET_SHA" "$PREVIOUS_SHA" 2>/dev/null; then
  log "Skipping stale deployment $TARGET_SHA; server is already at newer commit $PREVIOUS_SHA"
  exit 0
fi

requirements_hash() {
  if [ -f requirements.txt ]; then
    sha256sum requirements.txt | awk '{print $1}'
  else
    printf 'missing\n'
  fi
}

OLD_REQUIREMENTS_HASH="$(requirements_hash)"

if [ -x .venv/bin/python ] && [ -f scripts/backup_state.py ]; then
  log "Creating verified pre-deployment backup"
  PREDEPLOY_BACKUP="$(.venv/bin/python scripts/backup_state.py --print-path)"
  if [ -f .env ]; then
    install -m 600 .env "$PREDEPLOY_BACKUP/environment.env"
  fi
  log "Pre-deployment backup created"
fi

rollback() {
  local exit_code="$?"
  trap - ERR

  printf '\nERROR: deployment failed. Rolling back to %s\n' "$PREVIOUS_SHA" >&2
  git reset --hard "$PREVIOUS_SHA" || true

  if [ -n "$PREDEPLOY_BACKUP" ] && [ -f "$PREDEPLOY_BACKUP/environment.env" ]; then
    install -m 600 "$PREDEPLOY_BACKUP/environment.env" .env || true
  fi

  if [ -n "$OLD_VENV_BACKUP" ] && [ -d "$OLD_VENV_BACKUP" ]; then
    rm -rf .venv
    mv "$OLD_VENV_BACKUP" .venv || true
    OLD_VENV_BACKUP=""
  fi

  if [ -x .venv/bin/python ] && [ -f requirements.txt ]; then
    .venv/bin/python -m pip install -r requirements.txt >/dev/null 2>&1 || true
  fi

  sudo -n systemctl restart "$SERVICE_NAME" || true

  if wait_for_health 20; then
    printf 'Rollback health-check: OK\n' >&2
    exit "$exit_code"
  fi

  printf 'CRITICAL: rollback completed but service health-check is still failing.\n' >&2
  exit "$exit_code"
}
trap rollback ERR

log "Deploying commit $TARGET_SHA"
git reset --hard "$TARGET_SHA"
sync_navigation_config
sync_ai_config
NEW_REQUIREMENTS_HASH="$(requirements_hash)"

ensure_app_python
if ! venv_matches_app_python; then
  rebuild_runtime_venv
elif [ "$OLD_REQUIREMENTS_HASH" != "$NEW_REQUIREMENTS_HASH" ]; then
  log "requirements.txt changed; updating Python dependencies"
  .venv/bin/python -m pip install -r requirements.txt
else
  log "Python dependencies unchanged"
fi

log "Application runtime: $(.venv/bin/python -c 'import platform; print(platform.python_version())')"

log "Running server preflight"
.venv/bin/python -m compileall -q bot.py web_app.py config.py core handlers integrations modules scripts

log "Restarting $SERVICE_NAME"
sudo -n systemctl restart "$SERVICE_NAME"

log "Waiting for application health-check"
wait_for_health 30 || false

report_ai_status

log "Verifying services survive a VM reboot"
systemctl is-enabled --quiet "$SERVICE_NAME" || fail "$SERVICE_NAME is not enabled"
systemctl is-enabled --quiet caddy || fail "caddy is not enabled"

log "Creating verified state backup"
.venv/bin/python scripts/backup_state.py

trap - ERR
if [ -n "$OLD_VENV_BACKUP" ] && [ -d "$OLD_VENV_BACKUP" ]; then
  rm -rf "$OLD_VENV_BACKUP"
  OLD_VENV_BACKUP=""
fi
log "Deployment successful: $TARGET_SHA"
