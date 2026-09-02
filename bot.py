import asyncio
import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import TG_TOKEN, validate_config
from core.db import init_db
from handlers.voice import handle_voice
from logging_config import setup_logging
from modules.auth import OAuthServer, reconnect_command, start_command
from modules.router import handle_text
from modules.settings import (
    buffer_callback,
    buffer_command,
    calendar_settings_command,
    timezone_callback,
    timezone_command,
    workdays_callback,
    workdays_command,
    workhours_callback,
    workhours_command,
)

logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging()
    validate_config()
    init_db()

    oauth_server = OAuthServer(port=8080)
    oauth_server.start()

    application = Application.builder().token(TG_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("reconnect_google", reconnect_command))
    application.add_handler(CommandHandler("timezone", timezone_command))
    application.add_handler(CommandHandler("calendar_settings", calendar_settings_command))
    application.add_handler(CommandHandler("workhours", workhours_command))
    application.add_handler(CommandHandler("workdays", workdays_command))
    application.add_handler(CommandHandler("buffer", buffer_command))

    application.add_handler(CallbackQueryHandler(timezone_callback, pattern=r"^tz:"))
    application.add_handler(CallbackQueryHandler(workhours_callback, pattern=r"^wh:"))
    application.add_handler(CallbackQueryHandler(workdays_callback, pattern=r"^wd:"))
    application.add_handler(CallbackQueryHandler(buffer_callback, pattern=r"^buf:"))

    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

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
        oauth_server.stop()


if __name__ == "__main__":
    asyncio.run(main())
