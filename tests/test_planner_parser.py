from datetime import date, datetime, time

import pytest

from modules.planner.models import Intent, MissingField
from modules.planner.parser import PlannerParser


NOW = datetime(2026, 9, 2, 12, 0)  # Wednesday


@pytest.fixture
def parser():
    return PlannerParser("Europe/Moscow")


@pytest.mark.parametrize(
    ("text_value", "expected"),
    [
        ("Сегодня в 15 встреча", date(2026, 9, 2)),
        ("Завтра в 15 встреча", date(2026, 9, 3)),
        ("Послезавтра в 15 встреча", date(2026, 9, 4)),
        ("После завтра в 15 встреча", date(2026, 9, 4)),
        ("Через 2 дня в 15 встреча", date(2026, 9, 4)),
        ("Через три дня в 15 встреча", date(2026, 9, 5)),
        ("Через неделю в 15 встреча", date(2026, 9, 9)),
        ("Через 2 недели в 15 встреча", date(2026, 9, 16)),
    ],
)
def test_relative_dates(parser, text_value, expected):
    assert parser.parse(text_value, NOW).event_date == expected


@pytest.mark.parametrize(
    ("weekday", "expected"),
    [
        ("понедельник", date(2026, 9, 7)),
        ("вторник", date(2026, 9, 8)),
        ("среду", date(2026, 9, 9)),
        ("четверг", date(2026, 9, 3)),
        ("пятницу", date(2026, 9, 4)),
        ("субботу", date(2026, 9, 5)),
        ("воскресенье", date(2026, 9, 6)),
    ],
)
def test_all_weekdays(parser, weekday, expected):
    assert parser.parse(f"В {weekday} в 10 встреча", NOW).event_date == expected


def test_next_weekday_keeps_old_parser_semantics(parser):
    result = parser.parse("В следующую пятницу в 10 встреча", NOW)
    assert result.event_date == date(2026, 9, 11)


@pytest.mark.parametrize(
    ("text_value", "expected"),
    [
        ("Встреча 5 сентября в 14", date(2026, 9, 5)),
        ("Встреча 05.09 в 14", date(2026, 9, 5)),
        ("Встреча 05-09-2026 в 14", date(2026, 9, 5)),
        ("Встреча 05.09.27 в 14", date(2027, 9, 5)),
        ("Встреча 5 сентября 2027 в 14", date(2027, 9, 5)),
        ("Встреча 30 янв в 14", date(2027, 1, 30)),
    ],
)
def test_calendar_dates(parser, text_value, expected):
    assert parser.parse(text_value, NOW).event_date == expected


@pytest.mark.parametrize(
    ("text_value", "expected"),
    [
        ("Завтра встреча в 15", time(15, 0)),
        ("Завтра встреча в 9 утра", time(9, 0)),
        ("Завтра встреча в 9 вечера", time(21, 0)),
        ("Завтра встреча в 10:30", time(10, 30)),
        ("Завтра встреча 10.30", time(10, 30)),
        ("Завтра встреча 10-30", time(10, 30)),
        ("Завтра встреча в 10 30", time(10, 30)),
        ("Завтра встреча утром", time(9, 0)),
        ("Завтра встреча после обеда", time(15, 0)),
        ("Завтра встреча вечером", time(18, 0)),
    ],
)
def test_time_formats(parser, text_value, expected):
    assert parser.parse(text_value, NOW).event_time == expected


def test_relative_hours_set_date_and_time(parser):
    result = parser.parse("Напомни через два часа проверить почту", NOW)
    assert result.intent == Intent.CREATE_REMINDER
    assert result.event_date == date(2026, 9, 2)
    assert result.event_time == time(14, 0)
    assert result.title == "Проверить почту"


def test_date_is_not_mistaken_for_time(parser):
    result = parser.parse("Встреча 05.09.2026", NOW)
    assert result.event_date == date(2026, 9, 5)
    assert result.event_time is None
    assert result.missing == [MissingField.TIME]


def test_event_without_date_and_time_requests_both(parser):
    result = parser.parse("Встреча с Иваном", NOW)
    assert result.missing == [MissingField.DATE, MissingField.TIME]


def test_time_without_date_requests_only_date(parser):
    result = parser.parse("Позвонить Ивану в 15:00", NOW)
    assert result.intent == Intent.CREATE_TASK
    assert result.missing == [MissingField.DATE]


def test_task_with_date_does_not_invent_time(parser):
    result = parser.parse("Послезавтра подготовить отчёт", NOW)
    assert result.intent == Intent.CREATE_TASK
    assert result.event_date == date(2026, 9, 4)
    assert result.event_time is None
    assert result.missing == []


def test_task_without_schedule_is_valid(parser):
    result = parser.parse("Нужно подготовить отчёт", NOW)
    assert result.intent == Intent.CREATE_TASK
    assert result.event_date is None
    assert result.event_time is None
    assert result.missing == []


def test_vague_next_week_requests_weekday(parser):
    result = parser.parse("На следующей неделе встретиться с Иваном", NOW)
    assert result.intent == Intent.CREATE_EVENT
    assert MissingField.WEEKDAY in result.missing


def test_different_word_order(parser):
    result = parser.parse("С клиентом встреча в 14 седьмого не понимаю", NOW)
    assert result.intent == Intent.CREATE_EVENT
    assert result.event_time == time(14, 0)


def test_time_range_from_old_parser(parser):
    result = parser.parse("Завтра встреча с 10 до 11", NOW)
    assert result.event_time == time(10, 0)
    assert result.duration_minutes == 60


def test_invalid_date_is_not_accepted(parser):
    result = parser.parse("Встреча 31.02.2027 в 10", NOW)
    assert result.event_date is None
    assert MissingField.DATE in result.missing


def test_explicit_past_year_is_preserved_for_service_validation(parser):
    result = parser.parse("Встреча 1 сентября 2025 в 10", NOW)
    assert result.event_date == date(2025, 9, 1)


def test_year_boundary(parser):
    result = parser.parse("Встреча 2 января в 10", datetime(2026, 12, 31, 12, 0))
    assert result.event_date == date(2027, 1, 2)


def test_clarification_merges_only_missing_values(parser):
    pending = parser.parse("Встреча с Иваном в 15", NOW)
    result = parser.parse_clarification("завтра", pending, NOW)
    assert result.title == "Встреча с Иваном"
    assert result.event_date == date(2026, 9, 3)
    assert result.event_time == time(15, 0)
    assert result.missing == []
