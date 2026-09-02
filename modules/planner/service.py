"""Application service for planner commands."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from core.db import create_reminder, create_task
from integrations.google_calendar import GoogleCalendarAdapter

from .models import Intent, PlannerCommand
from .reminders import schedule_reminder


class PastScheduleError(ValueError):
    pass


class PlannerService:
    def __init__(self, timezone: str) -> None:
        self.timezone = ZoneInfo(timezone)
        self.calendar = GoogleCalendarAdapter(timezone)

    async def execute(self, command: PlannerCommand, user_id: int, chat_id: int, job_queue) -> str:
        if command.intent == Intent.CREATE_TASK:
            due = self._task_due(command)
            if isinstance(due, datetime):
                self._ensure_future(due)
            task_id = create_task(user_id, command.title, due)
            if due is None:
                return f"Задача №{task_id} создана: «{command.title}». Без срока."
            if isinstance(due, date) and not isinstance(due, datetime):
                return f"Задача №{task_id} создана на {due.strftime('%d.%m.%Y')}: «{command.title}»."
            return f"Задача №{task_id} создана на {due.strftime('%d.%m.%Y %H:%M')}: «{command.title}»."

        scheduled = self._scheduled(command)
        self._ensure_future(scheduled)

        if command.intent == Intent.CREATE_REMINDER:
            if job_queue is None:
                raise RuntimeError("Telegram JobQueue is unavailable")
            reminder_id = create_reminder(user_id, chat_id, command.title, scheduled)
            schedule_reminder(job_queue, reminder_id, chat_id, command.title, scheduled)
            return f"Напомню {scheduled.strftime('%d.%m.%Y в %H:%M')}: «{command.title}»."

        await self.calendar.create_event(
            user_id=user_id,
            title=command.title,
            start=scheduled,
            duration_minutes=command.duration_minutes,
        )
        return f"Событие «{command.title}» добавлено на {scheduled.strftime('%d.%m.%Y в %H:%M')}."

    def _scheduled(self, command: PlannerCommand) -> datetime:
        value = command.scheduled_at
        if value is None:
            raise ValueError("An exact date and time are required")
        return value.replace(tzinfo=self.timezone)

    def _task_due(self, command: PlannerCommand) -> date | datetime | None:
        if command.event_date and command.event_time:
            return self._scheduled(command)
        return command.event_date

    def _ensure_future(self, value: datetime) -> None:
        if value <= datetime.now(self.timezone):
            raise PastScheduleError("The requested time is in the past")
