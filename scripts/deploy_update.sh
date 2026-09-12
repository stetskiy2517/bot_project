#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-personal-secretary}"
BRANCH="${BRANCH:-main}"
TARGET_SHA="${1:-}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/api/health}"
PREVIOUS_SHA=""

log() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

cd "$PROJECT_DIR"

command -v git >/dev/null 2>&1 || fail "git is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v sudo >/dev/null 2>&1 || fail "sudo is required"

if [ -z "$TARGET_SHA" ]; then
  fail "Target commit SHA is required"
fi

log "Fetching $BRANCH from GitHub"
git fetch --prune origin "$BRANCH"
git cat-file -e "${TARGET_SHA}^{commit}" 2>/dev/null || fail "Target commit $TARGET_SHA is not available"
git merge-base --is-ancestor "$TARGET_SHA" "origin/$BRANCH" \
  || fail "Target commit $TARGET_SHA is not part of origin/$BRANCH"

PREVIOUS_SHA="$(git rev-parse HEAD)"
if [ "$PREVIOUS_SHA" = "$TARGET_SHA" ]; then
  log "Commit $TARGET_SHA is already deployed"
  exit 0
fi

# A delayed workflow must never roll production back over a newer deployment.
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

  for _ in $(seq 1 20); do
    if curl -fsS --connect-timeout 2 --max-time 4 "$HEALTH_URL" >/dev/null 2>&1; then
      printf 'Rollback health-check: OK\n' >&2
      exit "$exit_code"
    fi
    sleep 1
  done

  printf 'CRITICAL: rollback completed but service health-check is still failing.\n' >&2
  exit "$exit_code"
}
trap rollback ERR

log "Deploying commit $TARGET_SHA"
git reset --hard "$TARGET_SHA"
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
.venv/bin/python -m compileall -q bot.py web_app.py config.py core handlers integrations modules

log "Restarting $SERVICE_NAME"
sudo -n systemctl restart "$SERVICE_NAME"

log "Waiting for application health-check"
HEALTHY=0
for _ in $(seq 1 30); do
  if curl -fsS --connect-timeout 2 --max-time 4 "$HEALTH_URL" >/dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  sleep 1
done

if [ "$HEALTHY" -ne 1 ]; then
  false
fi

trap - ERR
log "Deployment successful: $TARGET_SHA"
