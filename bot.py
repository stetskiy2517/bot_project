import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import TG_TOKEN, validate_config
from core.db import init_db
from logging_config import setup_logging
from modules.router import handle_text
from modules.settings import timezone_callback, timezone_command


logger = logging.getLogger(__name__)


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработчик команды /start."""

    if update.message:
        await update.message.reply_text(
            "Бот запущен. После подключения Google Calendar настрой часовой пояс командой /timezone."
        )


async def main() -> None:
    """Запуск Telegram-бота."""

    setup_logging()
    validate_config()
    init_db()

    application = (
        Application.builder()
        .token(TG_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("timezone", timezone_command))
    application.add_handler(CallbackQueryHandler(timezone_callback, pattern=r"^tz:"))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    logger.info("Bot started")

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
