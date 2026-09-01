from telegram import Update
from telegram.ext import ContextTypes


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Temporary diagnostic router.

    Only the router is being tested at this stage.
    """
    if not update.message or not update.message.text:
        return

    await update.message.reply_text("MODULAR_ROUTER_OK")
