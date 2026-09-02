import asyncio
import logging

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import TG_TOKEN, validate_config
from core.db import init_db
from handlers.voice import handle_voice
from integrations.google_auth import start_command, start_http_server
from logging_config import setup_logging
from modules.planner.handlers import cancel_command, planner_help
from modules.planner.reminders import restore_reminders
from modules.router import handle_text


logger = logging.getLogger(__name__)


async def handle_error(update: object, context) -> None:
    """Log unexpected Telegram handler errors and return a safe message."""
    logger.error("Unhandled Telegram error", exc_info=context.error)
    message = getattr(update, "effective_message", None)
    if message:
        await message.reply_text("Не удалось обработать сообщение. Попробуйте ещё раз.")


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

    application.add_handler(
        CommandHandler("start", start_command)
    )
    application.add_handler(CommandHandler("planner", planner_help))
    application.add_handler(CommandHandler("cancel", cancel_command))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_error_handler(handle_error)

    logger.info("Bot started")

    oauth_runner = None
    await application.initialize()
    try:
        await application.start()
        await application.updater.start_polling()
        restore_reminders(application)
        oauth_runner = await start_http_server()
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        if application.updater.running:
            await application.updater.stop()
        if oauth_runner is not None:
            await oauth_runner.cleanup()
        if application.running:
            await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
