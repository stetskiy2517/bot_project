from googleapiclient.discovery import build
import json
from core.db import cursor
from config import CATEGORY_COLORS

def get_user_service(user_id):
    cursor.execute("SELECT google_credentials FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row: return None
    creds = json.loads(row[0])
    service = build("calendar", "v3", credentials=creds)
    return service

def get_user_calendar_id(user_id):
    cursor.execute("SELECT google_calendar_id FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row: return None
    return row[0]

def create_google_event(user_id, title, start, end, category):
    service = get_user_service(user_id)
    calendar_id = get_user_calendar_id(user_id)
    if not service or not calendar_id: return None

    event = {
        "summary": title,
        "start":{"dateTime":start.isoformat(),"timeZone":"Europe/Saratov"},
        "end":{"dateTime":end.isoformat(),"timeZone":"Europe/Saratov"},
        "colorId": CATEGORY_COLORS.get(category,"6")
    }
    created_event = service.events().insert(calendarId=calendar_id, body=event).execute()
    return created_event["id"]
