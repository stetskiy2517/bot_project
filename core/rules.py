from config import WORK_START, WORK_END

def is_work_time(start, end):
    if start.weekday() >= 5:
        return False
    return WORK_START <= start.hour < WORK_END and end.hour <= WORK_END

def default_priority(category):
    if category == "дети":
        return 10
    if category == "работа":
        return 7
    return 5
