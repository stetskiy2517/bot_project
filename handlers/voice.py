import os
import time
import logging
import requests
import re
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
BASE_URL = "https://api.assemblyai.com"


# ---------- FIX: normalize time like 22.00 -> 22:00 ----------
def normalize_time_format(text: str) -> str:
    return re.sub(
        r'\b([01]?\d|2[0-3])\.(\d{2})\b',
        r'\1:\2',
        text
    )


# ---------- AssemblyAI ----------
def transcribe_audio(file_path: str) -> str:
    if not ASSEMBLYAI_API_KEY:
        raise RuntimeError("ASSEMBLYAI_API_KEY not set")

    # 1️⃣ Upload
    with open(file_path, "rb") as f:
        upload = requests.post(
            f"{BASE_URL}/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            data=f,
            timeout=60,
        )

    if upload.status_code != 200:
        raise RuntimeError("Ошибка загрузки аудио")

    audio_url = upload.json()["upload_url"]

    # 2️⃣ Transcription (RU)
    transcript = requests.post(
        f"{BASE_URL}/v2/transcript",
        headers={
            "authorization": ASSEMBLYAI_API_KEY,
            "content-type": "application/json",
        },
        json={
            "audio_url": audio_url,
            "language_code": "ru",
            "speech_model": "universal",
        },
        timeout=30,
    ).json()

    transcript_id = transcript["id"]

    # 3️⃣ Poll
    while True:
        result = requests.get(
            f"{BASE_URL}/v2/transcript/{transcript_id}",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            timeout=30,
        ).json()

        status = result.get("status")

        if status == "completed":
            return result.get("text", "").strip()

        if status == "error":
            raise RuntimeError(result.get("error"))

        time.sleep(2)


# ---------- Telegram ----------
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)

        file_path = f"/tmp/{voice.file_id}.oga"
        await file.download_to_drive(file_path)

        text = transcribe_audio(file_path)

        if not text:
            await update.message.reply_text("❌ Не удалось распознать речь")
            return

        # 🔥 FIX APPLY HERE
        text = normalize_time_format(text)

        await update.message.reply_text(f"🗣 Распознано: {text}")

        from app import handle_text

        class FakeMessage:
            def __init__(self, text, user):
                self.text = text
                self.from_user = user
                self.chat_id = update.effective_chat.id

            async def reply_text(self, *args, **kwargs):
                await update.message.reply_text(*args, **kwargs)

        fake_update = Update(
            update.update_id,
            message=FakeMessage(text, update.effective_user),
        )

        await handle_text(fake_update, context)

    except Exception:
        logger.exception("Ошибка распознавания голоса")
        await update.message.reply_text("❌ Ошибка распознавания голоса")
