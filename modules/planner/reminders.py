"""Persistent Telegram reminders backed by the bot JobQueue."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from telegram.ext import Application, CallbackContext

from core.db import mark_reminder_delivered, pending_reminders


logger = logging.getLogger(__name__)


async def deliver_reminder(context: CallbackContext) -> None:
    data = context.job.data
    try:
        await context.bot.send_message(
            chat_id=data["chat_id"],
            text=f"⏰ Напоминание: {data['title']}",
        )
        mark_reminder_delivered(data["reminder_id"])
    except Exception:
        logger.exception("Could not deliver reminder %s", data.get("reminder_id"))


def schedule_reminder(job_queue, reminder_id: int, chat_id: int, title: str, when: datetime) -> None:
    now = datetime.now(when.tzinfo)
    if when <= now:
        when = now + timedelta(seconds=1)
    job_queue.run_once(
        deliver_reminder,
        when=when,
        data={"reminder_id": reminder_id, "chat_id": chat_id, "title": title},
        name=f"planner-reminder-{reminder_id}",
    )


def restore_reminders(application: Application) -> None:
    if application.job_queue is None:
        logger.error("JobQueue is unavailable; reminders cannot be restored")
        return
    for reminder in pending_reminders():
        schedule_reminder(
            application.job_queue,
            reminder["id"],
            reminder["chat_id"],
            reminder["title"],
            datetime.fromisoformat(reminder["remind_at"]),
        )
