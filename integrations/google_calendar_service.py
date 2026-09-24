"""Google Calendar service factory with safe revoked-token handling."""

from __future__ import annotations

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from core.db import clear_google_token, get_google_token


class GoogleAuthRequired(PermissionError):
    """Google Calendar credentials are missing or must be re-authorized."""


def _reauth_required(error: BaseException) -> bool:
    text = str(error or "").casefold()
    return (
        "invalid_grant" in text
        or "expired or revoked" in text
        or "token has been expired or revoked" in text
    )


def build_google_calendar_service(user_id: int):
    token = get_google_token(int(user_id))
    if not token:
        raise GoogleAuthRequired("GOOGLE_AUTH_REQUIRED")

    credentials = Credentials.from_authorized_user_info(token)
    original_refresh = credentials.refresh

    def refresh_with_revocation_handling(request):
        try:
            return original_refresh(request)
        except RefreshError as exc:
            if _reauth_required(exc):
                clear_google_token(int(user_id))
                raise PermissionError("GOOGLE_AUTH_REQUIRED") from exc
            raise

    credentials.refresh = refresh_with_revocation_handling
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)
