import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]

def get_calendar_service(user_id: int):
    path = f"tokens/{user_id}.json"
    if not os.path.exists(path):
        return None
    creds = Credentials.from_authorized_user_file(path, SCOPES)
    return build("calendar", "v3", credentials=creds)
