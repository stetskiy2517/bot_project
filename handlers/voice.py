import os
import time
import logging
import requests
import re

from telegram import Update
from telegram.ext import ContextTypes

from modules.planner import handle_text as handle_planner_text

logger = logging.getLogger(__name__)

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
BASE_URL = "https://api.assemblyai.com"


def normalize_time_format(text: str) -> str:
    """Нормализовать распознанное время 22.00 -> 22:00."""
    return re.sub(r"\b([01]?\d|2[0-3])\.(\d{2})\b", r"\1:\2", text)


def transcribe_audio(file_path: str) -> str:
    if not ASSEMBLYAI_API_KEY:
        raise RuntimeError("ASSEMBLYAI_API_KEY not set")

    with open(file_path, "rb") as audio:
        upload = requests.post(
            f"{BASE_URL}/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            data=audio,
            timeout=60,
        )
    upload.raise_for_status()
    audio_url = upload.json()["upload_url"]

    transcript_response = requests.post(
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
    )
    transcript_response.raise_for_status()
    transcript_id = transcript_response.json()["id"]

    while True:
        response = requests.get(
            f"{BASE_URL}/v2/transcript/{transcript_id}",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        status = result.get("status")
        if status == "completed":
            return result.get("text", "").strip()
        if status == "error":
            raise RuntimeError(result.get("error") or "Ошибка распознавания речи")
        time.sleep(2)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Распознать голос и передать текст в то же ядро, что и обычное сообщение."""
    file_path = None
    try:
        if not update.message or not update.message.voice:
            return

        voice = update.message.voice
        telegram_file = await context.bot.get_file(voice.file_id)
        file_path = f"/tmp/{voice.file_id}.oga"
        await telegram_file.download_to_drive(file_path)

        text = normalize_time_format(transcribe_audio(file_path))
        if not text:
            await update.message.reply_text("Не удалось распознать речь.")
            return

        await update.message.reply_text(f"Распознано: {text}")
        handled = await handle_planner_text(update, context, text=text)
        if not handled:
            await update.message.reply_text(
                "Не понял календарную команду. Скажи, например: «поставь врача завтра в 19:00»."
            )
    except Exception:
        logger.exception("Voice processing failed")
        await update.message.reply_text("Не удалось обработать голосовое сообщение.")
    finally:
        if file_path:
            try:
                os.remove(file_path)
            except OSError:
                pass
