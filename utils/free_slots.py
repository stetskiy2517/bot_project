from datetime import timedelta

def find_next_free_slot(service, start_dt, duration_minutes=60):
    """
    Ищет ближайший свободный слот в календаре
    """

    time_min = start_dt.isoformat()
    time_max = (start_dt + timedelta(days=7)).isoformat()

    events = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    current_start = start_dt
    duration = timedelta(minutes=duration_minutes)

    for event in events.get("items", []):
        event_start = event["start"].get("dateTime")
        event_end = event["end"].get("dateTime")

        if not event_start or not event_end:
            continue

        event_start = event_start.replace("Z", "+00:00")
        event_end = event_end.replace("Z", "+00:00")

        event_start_dt = start_dt.fromisoformat(event_start)
        event_end_dt = start_dt.fromisoformat(event_end)

        # если между текущим временем и следующим событием есть окно
        if event_start_dt - current_start >= duration:
            return current_start

        current_start = max(current_start, event_end_dt)

    return current_start
