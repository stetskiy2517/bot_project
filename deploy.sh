#!/bin/bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/bot_project}"
BRANCH="${BRANCH:-main}"

cd "$PROJECT_DIR"

echo "==> Updating code"
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

if [ ! -f .env ]; then
  echo "ERROR: .env is missing in $PROJECT_DIR"
  echo "Copy .env.example to .env and fill in the values first."
  exit 1
fi

echo "==> Validating required environment variables"
set -a
. ./.env
set +a

: "${PUBLIC_HOST:?PUBLIC_HOST is required in .env}"
: "${GOOGLE_CLIENT_ID:?GOOGLE_CLIENT_ID is required in .env}"
: "${GOOGLE_CLIENT_SECRET:?GOOGLE_CLIENT_SECRET is required in .env}"
: "${ASSEMBLYAI_API_KEY:?ASSEMBLYAI_API_KEY is required in .env for voice recognition}"
: "${WEB_SESSION_SECRET:?WEB_SESSION_SECRET is required in .env}"

export BASE_URL="${BASE_URL:-https://$PUBLIC_HOST}"
export REDIRECT_URI="${REDIRECT_URI:-$BASE_URL/oauth2callback}"

if ! grep -q '^BASE_URL=' .env; then echo "BASE_URL=$BASE_URL" >> .env; fi
if ! grep -q '^REDIRECT_URI=' .env; then echo "REDIRECT_URI=$REDIRECT_URI" >> .env; fi

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

echo "==> Deployment complete"
echo "URL: https://$PUBLIC_HOST"
echo "Google OAuth callback: $REDIRECT_URI"
echo "Voice recognition: AssemblyAI configured"
docker compose ps
