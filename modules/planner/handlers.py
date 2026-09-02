"""Telegram entry point for AI Smart Planner."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import TIMEZONE
from integrations.google_calendar import GoogleAuthRequired

from .models import Intent, MissingField, PlannerCommand
from .parser import PlannerParser
from .service import PastScheduleError, PlannerService


logger = logging.getLogger(__name__)
PENDING_KEY = "planner_pending"
parser = PlannerParser(TIMEZONE)
service = PlannerService(TIMEZONE)


async def handle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text_override: str | None = None,
) -> bool:
    if not update.message or not update.effective_user:
        return False
    text = (text_override if text_override is not None else update.message.text or "").strip()
    if not text:
        return False

    pending_data = context.user_data.get(PENDING_KEY)
    if pending_data and text.lower() in {"отмена", "отмени", "не надо"}:
        context.user_data.pop(PENDING_KEY, None)
        await update.message.reply_text("Хорошо, отменил.")
        return True
    if pending_data:
        command = parser.parse_clarification(text, PlannerCommand.from_dict(pending_data))
    else:
        command = parser.parse(text)

    if command.intent == Intent.UNKNOWN and not pending_data:
        return False

    if command.needs_clarification:
        context.user_data[PENDING_KEY] = command.to_dict()
        await update.message.reply_text(_clarification_question(command))
        return True

    context.user_data.pop(PENDING_KEY, None)
    try:
        result = await service.execute(
            command,
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
            job_queue=context.job_queue,
        )
    except GoogleAuthRequired:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
    except PastScheduleError:
        await update.message.reply_text("Это время уже прошло. Укажите будущую дату или время.")
    except Exception:
        logger.exception("Planner action failed for user %s", update.effective_user.id)
        await update.message.reply_text("Не удалось выполнить действие. Попробуйте ещё раз.")
    else:
        await update.message.reply_text(result)
    return True


async def planner_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "Я умею создавать события, задачи и напоминания.\n\n"
        "Примеры:\n"
        "• Завтра в 15 встреча с Иваном\n"
        "• Послезавтра подготовить отчёт\n"
        "• Напомни через два часа проверить почту\n\n"
        "Если я задал уточняющий вопрос, отменить действие можно командой /cancel."
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    had_pending = context.user_data.pop(PENDING_KEY, None) is not None
    if update.message:
        await update.message.reply_text(
            "Текущее действие отменено." if had_pending else "Нет действия, которое нужно отменить."
        )


def _clarification_question(command: PlannerCommand) -> str:
    missing = set(command.missing)
    if MissingField.WEEKDAY in missing:
        return "В какой день следующей недели это запланировать?"
    if MissingField.DATE in missing and MissingField.TIME in missing:
        return "На какую дату и время это запланировать?"
    if MissingField.DATE in missing:
        return "На какую дату это запланировать?"
    if MissingField.TIME in missing:
        return "На какое время это запланировать?"
    return "Как назвать задачу или событие?"
