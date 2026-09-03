"""Telegram voice transport.

Voice is only an input channel: speech is transcribed by the shared integration
and the resulting text is passed to the same central router as typed messages.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

from integrations.speech import normalize_time_format, transcribe_audio
from modules.router import route_text

logger = logging.getLogger(__name__)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transcribe a Telegram voice message and pass its text to the central router."""
    file_path: str | None = None
    try:
        if not update.message or not update.message.voice:
            return

        voice = update.message.voice
        telegram_file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as tmp:
            file_path = tmp.name

        await telegram_file.download_to_drive(file_path)

        transcript = await asyncio.to_thread(transcribe_audio, file_path)
        text = normalize_time_format(transcript)
        if not text:
            await update.message.reply_text("Не удалось распознать речь.")
            return

        await update.message.reply_text(f"Распознано: {text}")
        handled = await route_text(update, context, text=text)
        if not handled:
            await update.message.reply_text(
                "Не понял команду. Скажи иначе или уточни, что нужно сделать."
            )
    except Exception:
        logger.exception("Voice processing failed")
        if update.message:
            await update.message.reply_text("Не удалось обработать голосовое сообщение.")
    finally:
        if file_path:
            try:
                os.remove(file_path)
            except OSError:
                logger.debug("Temporary voice file was already removed: %s", file_path)
