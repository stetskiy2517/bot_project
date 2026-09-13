#!/usr/bin/env bash
set -Eeuo pipefail

BRANCH="${BRANCH:-main}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${SUDO_USER:-${USER:-$(id -un)}}"
SERVICE_NAME="personal-secretary"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CADDY_FILE="/etc/caddy/Caddyfile"

cd "$PROJECT_DIR"

log() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then
  fail "Run this script as the normal server user, not root: bash deploy.sh"
fi

command -v sudo >/dev/null 2>&1 || fail "sudo is required"
sudo -v

log "Updating code from GitHub"
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  printf 'Created .env from .env.example.\n'
fi

upsert_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
  export "$key=$value"
}

set -a
# shellcheck disable=SC1091
. ./.env
set +a

log "Preparing public HTTPS address"
PUBLIC_IP="$(curl -4fsS --connect-timeout 5 --max-time 10 https://icanhazip.com | tr -d '[:space:]')" || true
if ! printf '%s' "$PUBLIC_IP" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
  fail "Could not detect the server public IPv4"
fi

if [ -z "${PUBLIC_HOST:-}" ] || [[ "$PUBLIC_HOST" == *.sslip.io ]]; then
  PUBLIC_HOST="${PUBLIC_IP}.sslip.io"
  upsert_env PUBLIC_HOST "$PUBLIC_HOST"
fi

if [ -z "${BASE_URL:-}" ] || [[ "$BASE_URL" == *sslip.io* ]]; then
  BASE_URL="https://$PUBLIC_HOST"
  upsert_env BASE_URL "$BASE_URL"
fi

if [ -z "${REDIRECT_URI:-}" ] || [[ "$REDIRECT_URI" == *sslip.io* ]]; then
  REDIRECT_URI="$BASE_URL/oauth2callback"
  upsert_env REDIRECT_URI "$REDIRECT_URI"
fi

upsert_env WEB_HOST "127.0.0.1"
upsert_env WEB_PORT "8080"
upsert_env DB_PATH "data/bot.db"

if [ -z "${WEB_SESSION_SECRET:-}" ]; then
  WEB_SESSION_SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  upsert_env WEB_SESSION_SECRET "$WEB_SESSION_SECRET"
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

missing=()
for key in GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET ASSEMBLYAI_API_KEY WEB_SESSION_SECRET; do
  if [ -z "${!key:-}" ]; then
    missing+=("$key")
  fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  printf '\nMissing required values in %s/.env:\n' "$PROJECT_DIR" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  printf '\nFill them once, then run: bash deploy.sh\n' >&2
  exit 1
fi
chmod 600 .env

log "Installing Ubuntu prerequisites"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip curl ca-certificates \
  debian-keyring debian-archive-keyring apt-transport-https gnupg

if ! command -v caddy >/dev/null 2>&1; then
  log "Installing Caddy from the official repository"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
  sudo apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y caddy
fi

log "Preparing Python environment"
rm -rf .venv
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt
mkdir -p data
if [ -f bot.db ] && [ ! -f data/bot.db ]; then
  mv bot.db data/bot.db
  printf 'Migrated existing bot.db to data/bot.db\n'
fi

log "Running preflight checks"
bash -n scripts/deploy_update.sh
.venv/bin/python -m compileall -q bot.py web_app.py config.py core handlers integrations modules scripts tests
TEST_DB="/tmp/personal-secretary-deploy-test-$$.db"
rm -f "$TEST_DB"
DB_PATH="$TEST_DB" .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -q
rm -f "$TEST_DB"

log "Stopping old project processes if present"
sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
if command -v docker >/dev/null 2>&1; then
  sudo docker rm -f personal-secretary-proxy personal-secretary-web telegram_bot 2>/dev/null || true
fi
sudo systemctl disable --now nginx apache2 2>/dev/null || true

log "Installing systemd service"
sudo tee "$SERVICE_FILE" >/dev/null <<EOF_SERVICE
[Unit]
Description=Personal Secretary web MVP
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/.venv/bin/hypercorn web_app:app --bind 127.0.0.1:8080
Restart=always
RestartSec=3
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF_SERVICE

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME" >/dev/null
sudo systemctl restart "$SERVICE_NAME"

log "Configuring Caddy HTTPS reverse proxy"
sudo mkdir -p /etc/caddy
sudo tee "$CADDY_FILE" >/dev/null <<EOF_CADDY
$PUBLIC_HOST {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8080
}
EOF_CADDY
sudo caddy fmt --overwrite "$CADDY_FILE"
sudo caddy validate --config "$CADDY_FILE" --adapter caddyfile
sudo systemctl enable caddy >/dev/null
sudo systemctl restart caddy

log "Checking local application"
for _ in $(seq 1 30); do
  if curl -fsS --connect-timeout 2 --max-time 4 http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS --connect-timeout 2 --max-time 4 http://127.0.0.1:8080/api/health >/dev/null \
  || { sudo journalctl -u "$SERVICE_NAME" -n 100 --no-pager; fail "Web application did not start"; }

log "Checking HTTPS through local Caddy"
HTTPS_READY=0
for _ in $(seq 1 30); do
  if curl -fsS --connect-timeout 3 --max-time 5 \
      --resolve "$PUBLIC_HOST:443:127.0.0.1" \
      "https://$PUBLIC_HOST/api/health" >/dev/null 2>&1; then
    HTTPS_READY=1
    break
  fi
  sleep 2
done

if [ "$HTTPS_READY" -ne 1 ]; then
  printf '\nCaddy status:\n' >&2
  sudo systemctl --no-pager --full status caddy | sed -n '1,20p' >&2 || true
  printf '\nCaddy logs:\n' >&2
  sudo journalctl -u caddy -n 80 --no-pager >&2 || true
  fail "Caddy could not serve a valid HTTPS certificate. Verify inbound TCP ports 80 and 443 in cloud.ru."
fi

log "Creating verified state backup"
.venv/bin/python scripts/backup_state.py

log "Verifying reboot recovery"
systemctl is-enabled --quiet "$SERVICE_NAME" || fail "$SERVICE_NAME is not enabled"
systemctl is-enabled --quiet caddy || fail "caddy is not enabled"

log "MVP is online"
printf 'URL: %s\n' "https://$PUBLIC_HOST"
printf 'Health: %s\n' "https://$PUBLIC_HOST/api/health"
printf 'Google OAuth redirect URI: %s\n' "$REDIRECT_URI"
printf '\nService status:\n'
sudo systemctl --no-pager --full status "$SERVICE_NAME" | sed -n '1,12p'
printf '\nIMPORTANT: add this exact URI to Google OAuth Authorized redirect URIs:\n%s\n' "$REDIRECT_URI"
