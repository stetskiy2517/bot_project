from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import threading

_LOCATION_TTL = timedelta(minutes=15)
_lock = threading.RLock()
_locations: dict[int, "CurrentLocation"] = {}


@dataclass(frozen=True)
class CurrentLocation:
    latitude: float
    longitude: float
    accuracy_meters: float | None
    captured_at: datetime


def save_current_location(user_id: int, latitude: float, longitude: float, accuracy_meters: float | None = None) -> CurrentLocation:
    if any(isinstance(value, bool) for value in (latitude, longitude, accuracy_meters)):
        raise ValueError("Invalid location values")
    latitude = float(latitude)
    longitude = float(longitude)
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Некорректные координаты")
    accuracy = None if accuracy_meters is None else float(accuracy_meters)
    if accuracy is not None and (not math.isfinite(accuracy) or accuracy < 0):
        raise ValueError("Invalid location accuracy")
    location = CurrentLocation(latitude, longitude, accuracy, datetime.now(timezone.utc))
    with _lock:
        _locations[int(user_id)] = location
    return location


def get_current_location(user_id: int) -> CurrentLocation | None:
    with _lock:
        location = _locations.get(int(user_id))
        if not location:
            return None
        if datetime.now(timezone.utc) - location.captured_at > _LOCATION_TTL:
            _locations.pop(int(user_id), None)
            return None
        return location


def current_location_origin(user_id: int) -> str | None:
    location = get_current_location(user_id)
    if not location:
        return None
    return f"geo:{location.latitude:.7f},{location.longitude:.7f}"


def clear_current_location(user_id: int) -> None:
    with _lock:
        _locations.pop(int(user_id), None)
