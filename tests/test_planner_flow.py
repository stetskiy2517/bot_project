import asyncio
from datetime import datetime, timedelta
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

import core.db as db
import modules.planner.handlers as handlers
from modules.planner.models import Intent, PlannerCommand
from modules.planner.service import PastScheduleError, PlannerService


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, value):
        self.replies.append(value)


class FakeUpdate:
    def __init__(self, text):
        self.message = FakeMessage(text)
        self.effective_user = SimpleNamespace(id=101)
        self.effective_chat = SimpleNamespace(id=202)


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.job_queue = object()


class FakeService:
    def __init__(self):
        self.commands = []

    async def execute(self, command, **kwargs):
        self.commands.append(command)
        return "готово"


def test_two_turn_clarification(monkeypatch):
    fake_service = FakeService()
    monkeypatch.setattr(handlers, "service", fake_service)
    context = FakeContext()

    first = FakeUpdate("Встреча с Иваном в 15")
    assert asyncio.run(handlers.handle(first, context)) is True
    assert first.message.replies == ["На какую дату это запланировать?"]
    assert "planner_pending" in context.user_data

    second = FakeUpdate("завтра")
    assert asyncio.run(handlers.handle(second, context)) is True
    assert second.message.replies == ["готово"]
    assert fake_service.commands[0].title == "Встреча с Иваном"
    assert fake_service.commands[0].event_time.hour == 15
    assert "planner_pending" not in context.user_data


def test_unknown_message_is_left_for_router(monkeypatch):
    monkeypatch.setattr(handlers, "service", FakeService())
    update = FakeUpdate("Привет, как дела?")
    assert asyncio.run(handlers.handle(update, FakeContext())) is False
    assert update.message.replies == []


def test_pending_clarification_can_be_cancelled(monkeypatch):
    monkeypatch.setattr(handlers, "service", FakeService())
    context = FakeContext()
    context.user_data["planner_pending"] = handlers.parser.parse(
        "Встреча с Иваном"
    ).to_dict()
    update = FakeUpdate("отмена")

    assert asyncio.run(handlers.handle(update, context)) is True
    assert update.message.replies == ["Хорошо, отменил."]
    assert "planner_pending" not in context.user_data


def test_cancel_command_clears_pending_state():
    context = FakeContext()
    context.user_data["planner_pending"] = {"intent": "create_event"}
    update = FakeUpdate("/cancel")

    asyncio.run(handlers.cancel_command(update, context))

    assert update.message.replies == ["Текущее действие отменено."]
    assert context.user_data == {}


def test_planner_help_contains_examples():
    update = FakeUpdate("/planner")
    asyncio.run(handlers.planner_help(update, FakeContext()))
    assert "Завтра в 15 встреча с Иваном" in update.message.replies[0]


def test_task_is_persisted_without_invented_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "planner.db"))
    db.init_db()
    service = PlannerService("Europe/Moscow")
    command = PlannerCommand(intent=Intent.CREATE_TASK, title="Подготовить отчёт")

    response = asyncio.run(service.execute(command, 101, 202, None))

    with sqlite3.connect(db.DB_PATH) as connection:
        row = connection.execute("SELECT title, due_at FROM planner_tasks").fetchone()
    assert row == ("Подготовить отчёт", None)
    assert "Без срока" in response


def test_past_event_is_rejected_before_google_call():
    service = PlannerService("Europe/Moscow")
    command = PlannerCommand(
        intent=Intent.CREATE_EVENT,
        title="Встреча",
        event_date=datetime(2025, 1, 1).date(),
        event_time=datetime(2025, 1, 1, 10).time(),
    )
    with pytest.raises(PastScheduleError):
        asyncio.run(service.execute(command, 101, 202, None))


def test_event_reaches_calendar_adapter(monkeypatch):
    service = PlannerService("Europe/Moscow")
    calls = []

    async def create_event(**kwargs):
        calls.append(kwargs)
        return {"id": "google-event"}

    monkeypatch.setattr(service.calendar, "create_event", create_event)
    future = datetime.now(ZoneInfo("Europe/Moscow")) + timedelta(days=2)
    command = PlannerCommand(
        intent=Intent.CREATE_EVENT,
        title="Встреча с клиентом",
        event_date=future.date(),
        event_time=future.time().replace(microsecond=0),
        duration_minutes=30,
    )

    response = asyncio.run(service.execute(command, 101, 202, None))

    assert calls[0]["user_id"] == 101
    assert calls[0]["duration_minutes"] == 30
    assert calls[0]["start"].tzinfo is not None
    assert "Встреча с клиентом" in response


def test_google_token_is_encrypted_at_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "planner.db"))
    monkeypatch.setattr(db, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db.init_db()
    token = {"token": "secret-access-token", "refresh_token": "secret-refresh-token"}

    db.save_google_token(101, token)

    with sqlite3.connect(db.DB_PATH) as connection:
        stored = connection.execute("SELECT google_token FROM users").fetchone()[0]
    assert stored.startswith("fernet:")
    assert "secret-access-token" not in stored
    assert db.get_google_token(101) == token
