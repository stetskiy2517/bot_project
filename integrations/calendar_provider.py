"""Calendar provider boundary.

Legacy planner code still uses the existing Google implementation. New modules should use
this adapter so Yandex/CalDAV can be added without coupling new features to Google APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from core.db import get_google_token


class CalendarProvider(Protocol):
    name: str

    def create_event(self, event: dict) -> dict: ...
    def delete_event(self, event_id: str) -> None: ...
    def get_event(self, event_id: str) -> dict: ...
    def list_events(self, *, time_min: str, time_max: str, max_results: int = 2500) -> list[dict]: ...


@dataclass
class GoogleCalendarProvider:
    user_id: int
    name: str = "google"

    def _service(self):
        token = get_google_token(self.user_id)
        if not token:
            raise PermissionError("GOOGLE_AUTH_REQUIRED")
        credentials = Credentials.from_authorized_user_info(token)
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def create_event(self, event: dict) -> dict:
        return self._service().events().insert(calendarId="primary", body=event).execute()

    def delete_event(self, event_id: str) -> None:
        self._service().events().delete(calendarId="primary", eventId=str(event_id)).execute()

    def get_event(self, event_id: str) -> dict:
        return self._service().events().get(calendarId="primary", eventId=str(event_id)).execute()

    def list_events(self, *, time_min: str, time_max: str, max_results: int = 2500) -> list[dict]:
        result = self._service().events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=max(1, min(int(max_results), 2500)),
        ).execute()
        return list(result.get("items") or [])


def calendar_provider_name(user_id: int) -> str | None:
    return "google" if get_google_token(user_id) else None


def get_calendar_provider(user_id: int) -> CalendarProvider:
    provider = calendar_provider_name(user_id)
    if provider == "google":
        return GoogleCalendarProvider(int(user_id))
    raise PermissionError("CALENDAR_NOT_CONNECTED")


def supported_calendar_providers() -> list[dict]:
    return [
        {"id": "google", "implemented": True},
        {"id": "yandex_caldav", "implemented": False},
        {"id": "caldav", "implemented": False},
    ]
