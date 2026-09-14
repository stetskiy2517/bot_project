"""Google Routes provider used by Smart Planner navigation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re

import requests

from config import GOOGLE_MAPS_API_KEY

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
REQUEST_TIMEOUT_SECONDS = 8
TRAVEL_MODES = {
    "driving": "DRIVE",
    "walking": "WALK",
    "transit": "TRANSIT",
}


def configured() -> bool:
    return bool(GOOGLE_MAPS_API_KEY)


def _require_key() -> str:
    if not GOOGLE_MAPS_API_KEY:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is not configured")
    return GOOGLE_MAPS_API_KEY


def _normalize_address(value: str) -> str:
    address = " ".join(str(value).split()).strip(" ,.;")
    if not address:
        raise ValueError("Address is required")
    return address


def _future_departure_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("departure_at must contain a timezone")
    now = datetime.now(timezone.utc)
    departure = value.astimezone(timezone.utc)
    if departure <= now:
        departure = now + timedelta(seconds=1)
    return departure


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


GEO_ORIGIN_RE = re.compile(r"^geo:(?P<lat>[+-]?\d+(?:\.\d+)?),(?P<lon>[+-]?\d+(?:\.\d+)?)$")


def _waypoint(value: str) -> dict:
    raw = str(value).strip()
    match = GEO_ORIGIN_RE.fullmatch(raw)
    if not match:
        return {"address": _normalize_address(raw)}
    lat = float(match.group("lat"))
    lon = float(match.group("lon"))
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Invalid current location coordinates")
    return {"location": {"latLng": {"latitude": lat, "longitude": lon}}}
def _route_body(origin: str, destination: str, mode: str, departure_at: datetime) -> dict:
    travel_mode = TRAVEL_MODES.get(mode)
    if not travel_mode:
        raise ValueError(f"Unsupported travel mode: {mode}")

    body = {
        "origin": _waypoint(origin),
        "destination": _waypoint(destination),
        "travelMode": travel_mode,
        "languageCode": "ru",
        "regionCode": "ru",
        "units": "METRIC",
    }

    if mode == "driving":
        body["routingPreference"] = "TRAFFIC_AWARE"
        body["departureTime"] = _rfc3339(_future_departure_time(departure_at))
    elif mode == "transit":
        departure = _future_departure_time(departure_at)
        if departure > datetime.now(timezone.utc) + timedelta(days=100):
            raise ValueError("Google transit routes are available up to 100 days ahead")
        body["departureTime"] = _rfc3339(departure)

    return body


def _duration_seconds(value) -> int:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", str(value or ""))
    if not match:
        raise ValueError("Google route response does not contain a valid duration")
    return max(1, math.ceil(float(match.group(1))))


def _route_result(data: dict) -> tuple[int, int | None]:
    routes = data.get("routes") or []
    if not routes:
        raise ValueError("Google route was not found")
    route = routes[0]
    seconds = _duration_seconds(route.get("duration"))
    distance = route.get("distanceMeters")
    return seconds, int(distance) if distance is not None else None


def route(origin: str, destination: str, *, mode: str, departure_at: datetime) -> tuple[int, int | None]:
    response = requests.post(
        ROUTES_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": _require_key(),
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
        },
        json=_route_body(origin, destination, mode, departure_at),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return _route_result(response.json())


def estimate(address_from: str, address_to: str, *, mode: str, departure_at: datetime) -> tuple[int, int | None]:
    seconds, distance = route(address_from, address_to, mode=mode, departure_at=departure_at)
    return max(1, math.ceil(seconds / 60)), distance
