#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/bot_project}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/.local/share/personal-secretary/backups}"
JOURNAL_LIMIT="${JOURNAL_LIMIT:-100M}"

log() { printf '\n==> %s\n' "$*"; }

show_size() {
  local path="$1"
  if [ -e "$path" ]; then
    du -sh "$path" 2>/dev/null || true
  fi
}

log "Disk usage before cleanup"
df -h /
show_size "$BACKUP_DIR"
show_size "$HOME/.cache/pip"
show_size "$PROJECT_DIR/.git"
show_size "$PROJECT_DIR/.venv"
journalctl --disk-usage 2>/dev/null || true

cd "$PROJECT_DIR"

if [ -x .venv/bin/python ] && [ -f scripts/backup_state.py ]; then
  log "Pruning old/excess application backups"
  .venv/bin/python scripts/backup_state.py --prune-only
fi

if [ -d "$HOME/.cache/pip" ]; then
  log "Removing pip download cache"
  rm -rf -- "$HOME/.cache/pip"
fi

log "Removing stale Personal Secretary temp files"
find /tmp -maxdepth 1 -uid "$(id -u)" -type f -name 'personal-secretary-*' -mtime +1 -delete 2>/dev/null || true

log "Vacuuming system journal when permitted"
if sudo -n journalctl --vacuum-size="$JOURNAL_LIMIT"; then
  :
else
  echo "journal vacuum skipped: sudo permission is unavailable"
fi

log "Cleaning apt package cache when permitted"
if sudo -n apt-get clean; then
  :
else
  echo "apt cache cleanup skipped: sudo permission is unavailable"
fi

if command -v docker >/dev/null 2>&1; then
  docker_cmd=()
  if docker info >/dev/null 2>&1; then
    docker_cmd=(docker)
  elif sudo -n docker info >/dev/null 2>&1; then
    docker_cmd=(sudo -n docker)
  fi

  if [ "${#docker_cmd[@]}" -gt 0 ]; then
    log "Inspecting legacy Docker storage"
    "${docker_cmd[@]}" system df || true
    running="$("${docker_cmd[@]}" ps -q 2>/dev/null || true)"
    if [ -z "$running" ]; then
      log "Pruning unused Docker images, containers and build cache"
      "${docker_cmd[@]}" system prune -af || true
    else
      echo "Docker cleanup skipped: running containers detected"
    fi
  else
    echo "Docker cleanup skipped: Docker daemon is not accessible"
  fi
fi

log "Disk usage after cleanup"
df -h /
show_size "$BACKUP_DIR"
show_size "$HOME/.cache/pip"
journalctl --disk-usage 2>/dev/null || true
