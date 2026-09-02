"""Google Calendar adapter isolated from Telegram and planner parsing."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from core.db import get_google_token, save_google_token


logger = logging.getLogger(__name__)


class GoogleAuthRequired(PermissionError):
    pass


class GoogleCalendarAdapter:
    def __init__(self, timezone: str) -> None:
        self.timezone = timezone

    async def create_event(
        self,
        user_id: int,
        title: str,
        start: datetime,
        duration_minutes: int = 60,
    ) -> dict:
        return await asyncio.to_thread(
            self._create_event_sync,
            user_id,
            title,
            start,
            duration_minutes,
        )

    def _create_event_sync(
        self,
        user_id: int,
        title: str,
        start: datetime,
        duration_minutes: int,
    ) -> dict:
        token = get_google_token(user_id)
        if not token:
            raise GoogleAuthRequired("Google Calendar is not connected")

        credentials = Credentials.from_authorized_user_info(token)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            save_google_token(user_id, _credentials_dict(credentials))

        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        event = {
            "summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": self.timezone},
            "end": {
                "dateTime": (start + timedelta(minutes=duration_minutes)).isoformat(),
                "timeZone": self.timezone,
            },
        }
        return service.events().insert(calendarId="primary", body=event).execute()


def _credentials_dict(credentials: Credentials) -> dict:
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }
