from __future__ import annotations

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def _service(token: dict):
    credentials = Credentials.from_authorized_user_info(token, scopes=[GMAIL_SCOPE])
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def list_messages(token: dict, *, limit: int = 10, query: str | None = None, unread_only: bool = False) -> list[dict]:
    service = _service(token)
    parts = []
    if unread_only:
        parts.append("is:unread")
    if query:
        parts.append(str(query).strip())
    response = service.users().messages().list(userId="me", maxResults=max(1, min(int(limit), 30)), q=" ".join(parts)).execute()
    results = []
    for item in response.get("messages", []):
        message = service.users().messages().get(
            userId="me",
            id=item["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {entry.get("name", "").lower(): entry.get("value", "") for entry in message.get("payload", {}).get("headers", [])}
        results.append({
            "from": headers.get("from", ""),
            "subject": headers.get("subject") or "Без темы",
            "date": headers.get("date", ""),
            "preview": str(message.get("snippet") or "")[:500],
        })
    return results
