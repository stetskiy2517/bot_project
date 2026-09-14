#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-personal-secretary}"
BRANCH="${BRANCH:-main}"
TARGET_SHA="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/api/health}"
NAVIGATION_KEY_FILE="${NAVIGATION_KEY_FILE:-}"
PREVIOUS_SHA=""

log() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

cleanup_temp_secrets() {
  if [ -n "$NAVIGATION_KEY_FILE" ]; then
    rm -f -- "$NAVIGATION_KEY_FILE" 2>/dev/null || true
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

navigation_secret_present() {
  [ -n "$NAVIGATION_KEY_FILE" ] && [ -s "$NAVIGATION_KEY_FILE" ]
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
if [ "$PREVIOUS_SHA" = "$TARGET_SHA" ]; then
  if navigation_secret_present; then
    sync_navigation_config
    log "Restarting $SERVICE_NAME after secret update"
    sudo -n systemctl restart "$SERVICE_NAME"
    wait_for_health 30 || fail "Application health-check failed after navigation secret update"
    log "Navigation secret applied to already deployed commit $TARGET_SHA"
  else
    log "Commit $TARGET_SHA is already deployed"
  fi
  exit 0
fi

if git merge-base --is-ancestor "$TARGET_SHA" "$PREVIOUS_SHA" 2>/dev/null; then
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

rollback() {
  local exit_code="$?"
  trap - ERR

  printf '\nERROR: deployment failed. Rolling back to %s\n' "$PREVIOUS_SHA" >&2
  git reset --hard "$PREVIOUS_SHA" || true

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
NEW_REQUIREMENTS_HASH="$(requirements_hash)"

if [ ! -x .venv/bin/python ]; then
  log "Creating Python virtual environment"
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip setuptools wheel
  .venv/bin/python -m pip install -r requirements.txt
elif [ "$OLD_REQUIREMENTS_HASH" != "$NEW_REQUIREMENTS_HASH" ]; then
  log "requirements.txt changed; updating Python dependencies"
  .venv/bin/python -m pip install -r requirements.txt
else
  log "Python dependencies unchanged"
fi

log "Running server preflight"
.venv/bin/python -m compileall -q bot.py web_app.py config.py core handlers integrations modules scripts

log "Restarting $SERVICE_NAME"
sudo -n systemctl restart "$SERVICE_NAME"

log "Waiting for application health-check"
wait_for_health 30 || false

log "Verifying services survive a VM reboot"
systemctl is-enabled --quiet "$SERVICE_NAME" || fail "$SERVICE_NAME is not enabled"
systemctl is-enabled --quiet caddy || fail "caddy is not enabled"

log "Creating verified state backup"
.venv/bin/python scripts/backup_state.py

trap - ERR
log "Deployment successful: $TARGET_SHA"
