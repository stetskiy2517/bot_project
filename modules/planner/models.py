"""Domain models used by the planner parser and service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum


class Intent(str, Enum):
    CREATE_EVENT = "create_event"
    CREATE_TASK = "create_task"
    CREATE_REMINDER = "create_reminder"
    UNKNOWN = "unknown"


class MissingField(str, Enum):
    DATE = "date"
    TIME = "time"
    TITLE = "title"
    WEEKDAY = "weekday"


@dataclass(slots=True)
class PlannerCommand:
    intent: Intent
    title: str
    event_date: date | None = None
    event_time: time | None = None
    duration_minutes: int = 60
    missing: list[MissingField] = field(default_factory=list)
    date_text: str | None = None
    time_text: str | None = None
    confidence: float = 1.0

    @property
    def scheduled_at(self) -> datetime | None:
        if self.event_date is None or self.event_time is None:
            return None
        return datetime.combine(self.event_date, self.event_time)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.missing)

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "title": self.title,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "duration_minutes": self.duration_minutes,
            "missing": [item.value for item in self.missing],
            "date_text": self.date_text,
            "time_text": self.time_text,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "PlannerCommand":
        return cls(
            intent=Intent(value["intent"]),
            title=value["title"],
            event_date=date.fromisoformat(value["event_date"]) if value.get("event_date") else None,
            event_time=time.fromisoformat(value["event_time"]) if value.get("event_time") else None,
            duration_minutes=int(value.get("duration_minutes", 60)),
            missing=[MissingField(item) for item in value.get("missing", [])],
            date_text=value.get("date_text"),
            time_text=value.get("time_text"),
            confidence=float(value.get("confidence", 1.0)),
        )
