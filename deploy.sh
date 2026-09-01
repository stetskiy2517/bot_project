#!/bin/bash
set -e

# ====== Настройки ======
GITHUB_REPO="https://github.com/stetskiy2517/bot_project.git"
PROJECT_DIR="$HOME/bot_project"
DOCKER_IMAGE="bot_project-bot"
DOCKER_CONTAINER="telegram_bot"
RENDER_WEBHOOK="https://bot-project-bdub.onrender.com/telegram/webhook"

# ====== 1. Обновление кода ======
echo "📦 Обновляем код с GitHub..."
cd $PROJECT_DIR
git fetch origin main
git reset --hard origin/main

# ====== 2. Пересборка Docker ======
echo "🐳 Останавливаем старый контейнер..."
docker stop $DOCKER_CONTAINER 2>/dev/null || true
docker rm $DOCKER_CONTAINER 2>/dev/null || true

echo "🔧 Собираем новый Docker образ..."
docker build -t $DOCKER_IMAGE .

echo "▶️ Запускаем контейнер..."
docker run -d --name $DOCKER_CONTAINER -p 80:8080 $DOCKER_IMAGE

# ====== 3. Настройка Webhook ======
echo "🌐 Настраиваем Telegram webhook..."

docker exec $DOCKER_CONTAINER python3 - <<EOF
import os
from telegram import Bot

# Берем токен из переменных окружения контейнера
TG_TOKEN = os.environ.get("TG_TOKEN")
if not TG_TOKEN:
    raise ValueError("Не найден TG_TOKEN в переменных окружения Docker")

# Инициализация бота
bot = Bot(token=TG_TOKEN)

# Удаляем старый webhook
bot.delete_webhook()

# Устанавливаем новый webhook на Render
RENDER_WEBHOOK = os.environ.get("RENDER_WEBHOOK")
if not RENDER_WEBHOOK:
    raise ValueError("Не найден RENDER_WEBHOOK в переменных окружения Docker")

bot.set_webhook(RENDER_WEBHOOK)
print(f"Webhook установлен ✅ {RENDER_WEBHOOK}")
EOF

echo "✅ Деплой завершен. Бот работает на $RENDER_WEBHOOK"
