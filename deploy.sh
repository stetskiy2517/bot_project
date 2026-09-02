#!/bin/bash
set -e

# ====== Настройки ======
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_IMAGE="bot_project-bot"
DOCKER_CONTAINER="telegram_bot"

# ====== 1. Обновление кода ======
echo "📦 Обновляем код с GitHub..."
cd "$PROJECT_DIR"
git fetch origin main
git pull --ff-only origin main

# ====== 2. Пересборка Docker ======
echo "🐳 Останавливаем старый контейнер..."
docker stop "$DOCKER_CONTAINER" 2>/dev/null || true
docker rm "$DOCKER_CONTAINER" 2>/dev/null || true

echo "🔧 Собираем новый Docker образ..."
docker build -t "$DOCKER_IMAGE" .

echo "▶️ Запускаем контейнер..."
mkdir -p "$PROJECT_DIR/data"
docker run -d \
  --name "$DOCKER_CONTAINER" \
  --restart always \
  --env-file "$PROJECT_DIR/.env" \
  -e DB_PATH=/data/bot.db \
  -v "$PROJECT_DIR/data:/data" \
  -p 8080:8080 \
  "$DOCKER_IMAGE"

echo "✅ Деплой завершен. Telegram работает через polling, OAuth callback — на порту 8080."
