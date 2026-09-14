"""openrouteservice provider used by Smart Planner navigation."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import math
import re

import requests

from config import ORS_API_KEY

API_BASE_URL = "https://api.heigit.org"
GEOCODER_URL = f"{API_BASE_URL}/pelias/v1/search"
DIRECTIONS_URL = f"{API_BASE_URL}/openrouteservice/v2/directions"
REQUEST_TIMEOUT_SECONDS = 8
PROFILES = {
    "driving": "driving-car",
    "walking": "foot-walking",
}


def configured() -> bool:
    return bool(ORS_API_KEY)


def _require_key() -> str:
    if not ORS_API_KEY:
        raise RuntimeError("ORS_API_KEY is not configured")
    return ORS_API_KEY


def _normalize_address(value: str) -> str:
    address = " ".join(str(value).split()).strip(" ,.;")
    if not address:
        raise ValueError("Address is required")
    return address


def _point_from_geocode(data: dict) -> tuple[float, float]:
    features = data.get("features") or []
    if not features:
        raise ValueError("openrouteservice geocoder did not find the address")
    coordinates = ((features[0].get("geometry") or {}).get("coordinates") or [])
    if len(coordinates) < 2:
        raise ValueError("openrouteservice geocoder response does not contain coordinates")
    longitude = float(coordinates[0])
    latitude = float(coordinates[1])
    return longitude, latitude


GEO_ORIGIN_RE = re.compile(r"^geo:(?P<lat>[+-]?\d+(?:\.\d+)?),(?P<lon>[+-]?\d+(?:\.\d+)?)$")


def _coordinate_origin(value: str) -> tuple[float, float] | None:
    match = GEO_ORIGIN_RE.fullmatch(value.strip())
    if not match:
        return None
    lat = float(match.group("lat"))
    lon = float(match.group("lon"))
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Invalid current location coordinates")
    return lon, lat
@lru_cache(maxsize=512)
def geocode(address: str) -> tuple[float, float]:
    raw = str(address).strip()
    coordinate = _coordinate_origin(raw)
    if coordinate:
        return coordinate
    value = _normalize_address(raw)
    response = requests.get(
        GEOCODER_URL,
        headers={"Authorization": _require_key()},
        params={"text": value, "size": 1, "lang": "ru"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return _point_from_geocode(response.json())


def _profile_for_mode(mode: str) -> str:
    profile = PROFILES.get(mode)
    if profile:
        return profile
    if mode == "transit":
        raise ValueError("openrouteservice public API does not support public transport routing")
    raise ValueError(f"Unsupported travel mode: {mode}")


def _route_body(origin: tuple[float, float], destination: tuple[float, float]) -> dict:
    origin_lon, origin_lat = origin
    destination_lon, destination_lat = destination
    return {
        "coordinates": [
            [origin_lon, origin_lat],
            [destination_lon, destination_lat],
        ],
        "geometry": False,
        "instructions": False,
        "units": "m",
    }


def _route_result(data: dict) -> tuple[int, int | None]:
    routes = data.get("routes") or []
    if not routes:
        raise ValueError("openrouteservice route was not found")
    summary = routes[0].get("summary") or {}
    duration = summary.get("duration")
    if duration is None:
        raise ValueError("openrouteservice route response does not contain duration")
    seconds = max(1, math.ceil(float(duration)))
    distance = summary.get("distance")
    return seconds, math.ceil(float(distance)) if distance is not None else None


def route(
    origin: tuple[float, float],
    destination: tuple[float, float],
    *,
    mode: str,
    departure_at: datetime,
) -> tuple[int, int | None]:
    del departure_at
    profile = _profile_for_mode(mode)
    response = requests.post(
        f"{DIRECTIONS_URL}/{profile}",
        headers={
            "Authorization": _require_key(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=_route_body(origin, destination),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return _route_result(response.json())


def estimate(
    address_from: str,
    address_to: str,
    *,
    mode: str,
    departure_at: datetime,
) -> tuple[int, int | None]:
    origin = geocode(address_from)
    destination = geocode(address_to)
    seconds, distance = route(
        origin,
        destination,
        mode=mode,
        departure_at=departure_at,
    )
    return max(1, math.ceil(seconds / 60)), distance
