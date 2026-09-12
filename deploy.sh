#!/bin/bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/bot_project}"
BRANCH="${BRANCH:-main}"

cd "$PROJECT_DIR"

echo "==> Updating code"
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

set -a
. ./.env
set +a

upsert_env() {
  key="$1"
  value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
  export "$key=$value"
}

if [ -z "${PUBLIC_HOST:-}" ]; then
  echo "==> Detecting public IPv4 for temporary sslip.io hostname"
  PUBLIC_IP="$(curl -4fsS https://icanhazip.com | tr -d '[:space:]')"
  if ! printf '%s' "$PUBLIC_IP" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
    echo "ERROR: could not detect public IPv4"
    exit 1
  fi
  PUBLIC_HOST="${PUBLIC_IP}.sslip.io"
  upsert_env PUBLIC_HOST "$PUBLIC_HOST"
  echo "Temporary hostname: $PUBLIC_HOST"
fi

BASE_URL="${BASE_URL:-https://$PUBLIC_HOST}"
REDIRECT_URI="${REDIRECT_URI:-$BASE_URL/oauth2callback}"
upsert_env BASE_URL "$BASE_URL"
upsert_env REDIRECT_URI "$REDIRECT_URI"

echo "==> Validating required environment variables"
set -a
. ./.env
set +a

: "${GOOGLE_CLIENT_ID:?GOOGLE_CLIENT_ID is required in .env}"
: "${GOOGLE_CLIENT_SECRET:?GOOGLE_CLIENT_SECRET is required in .env}"
: "${ASSEMBLYAI_API_KEY:?ASSEMBLYAI_API_KEY is required in .env}"
: "${WEB_SESSION_SECRET:?WEB_SESSION_SECRET is required in .env}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker is not installed"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: Docker Compose plugin is not installed"
  exit 1
fi

echo "==> Building and starting containers"
docker compose up -d --build --remove-orphans

echo "==> Waiting for web healthcheck"
for i in $(seq 1 30); do
  status=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' personal-secretary-web 2>/dev/null || true)
  if [ "$status" = "healthy" ]; then
    echo "Web container is healthy"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: web container did not become healthy"
    docker compose ps
    docker compose logs --tail=100 web
    exit 1
  fi
  sleep 2
done

echo "==> Waiting for HTTPS"
for i in $(seq 1 30); do
  if curl -fsS "https://$PUBLIC_HOST/api/health" >/dev/null 2>&1; then
    echo "HTTPS is ready"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "WARNING: app is running, but HTTPS is not ready yet"
    docker compose logs --tail=100 caddy
    break
  fi
  sleep 2
done

echo "==> Deployment complete"
echo "URL: https://$PUBLIC_HOST"
echo "Google OAuth callback: $REDIRECT_URI"
echo "Voice recognition: AssemblyAI configured"
docker compose ps
