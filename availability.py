from datetime import timedelta

def check_busy(service, calendar_id, start, end):
    events = service.events().list(
        calendarId=calendar_id,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    return events.get("items", [])

def find_next_free_slot(service, calendar_id, start, duration_minutes=60, max_days=7):
    current = start

    for _ in range(max_days * 24):
        end = current + timedelta(minutes=duration_minutes)
        busy = check_busy(service, calendar_id, current, end)
        if not busy:
            return current
        current += timedelta(minutes=30)

    return None
