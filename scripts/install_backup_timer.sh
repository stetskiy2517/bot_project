#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
APP_USER="${APP_USER:-${SUDO_USER:-${USER:-$(id -un)}}}"
SERVICE_NAME="personal-secretary-backup"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}.timer"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
BACKUP_SCRIPT="$PROJECT_DIR/scripts/backup_state.py"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ -x "$PYTHON_BIN" ] || fail "Python virtualenv not found: $PYTHON_BIN"
[ -f "$BACKUP_SCRIPT" ] || fail "Backup script not found: $BACKUP_SCRIPT"
[ -f "$PROJECT_DIR/.env" ] || fail "Environment file not found: $PROJECT_DIR/.env"
command -v sudo >/dev/null 2>&1 || fail "sudo is required"

sudo -n tee "$SERVICE_FILE" >/dev/null <<EOF_SERVICE
[Unit]
Description=Personal Secretary state backup
After=local-fs.target

[Service]
Type=oneshot
User=$APP_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
UMask=0077
ExecStart=$PYTHON_BIN $BACKUP_SCRIPT
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
EOF_SERVICE

sudo -n tee "$TIMER_FILE" >/dev/null <<'EOF_TIMER'
[Unit]
Description=Daily Personal Secretary state backup

[Timer]
OnCalendar=*-*-* 03:15:00
RandomizedDelaySec=15m
Persistent=true
Unit=personal-secretary-backup.service

[Install]
WantedBy=timers.target
EOF_TIMER

sudo -n systemctl daemon-reload
sudo -n systemctl enable --now "$SERVICE_NAME.timer" >/dev/null

# Create and validate one snapshot immediately. If this fails, deployment should
# fail instead of silently pretending backups are configured.
sudo -n systemctl start "$SERVICE_NAME.service"
sudo -n systemctl is-enabled --quiet "$SERVICE_NAME.timer"
sudo -n systemctl is-active --quiet "$SERVICE_NAME.timer"

printf 'Backup timer installed and first snapshot completed.\n'
