"""2GIS geocoding and routing provider used by Smart Planner navigation."""

from __future__ import annotations

from datetime import datetime
import math
import re
import time

import requests

from config import DGIS_API_KEY

GEOCODER_URL = "https://catalog.api.2gis.com/3.0/items/geocode"
ROUTING_URL = "https://routing.api.2gis.com/routing/7.0.0/global"
PUBLIC_TRANSPORT_URL = "https://routing.api.2gis.com/public_transport/2.0"
REQUEST_TIMEOUT_SECONDS = 8
PUBLIC_TRANSPORT_TYPES = [
    "metro", "light_metro", "suburban_train", "aeroexpress", "tram", "bus",
    "trolleybus", "shuttle_bus", "monorail", "funicular_railway", "river_transport",
    "cable_car", "light_rail", "premetro", "mcc", "mcd", "pedestrian",
]


def configured() -> bool:
    return bool(DGIS_API_KEY)


def _require_key() -> str:
    if not DGIS_API_KEY:
        raise RuntimeError("DGIS_API_KEY is not configured")
    return DGIS_API_KEY


def _point_from_item(item: dict) -> tuple[float, float]:
    point = item.get("point") or {}
    if point.get("lat") is not None and point.get("lon") is not None:
        return float(point["lat"]), float(point["lon"])

    centroid = ((item.get("geometry") or {}).get("centroid"))
    if isinstance(centroid, dict) and centroid.get("lat") is not None and centroid.get("lon") is not None:
        return float(centroid["lat"]), float(centroid["lon"])
    if isinstance(centroid, str):
        match = re.search(r"POINT\s*\(\s*([+-]?[\d.]+)\s+([+-]?[\d.]+)\s*\)", centroid, re.IGNORECASE)
        if match:
            return float(match.group(2)), float(match.group(1))
    raise ValueError("2GIS geocoder response does not contain coordinates")


GEO_ORIGIN_RE = re.compile(r"^geo:(?P<lat>[+-]?\d+(?:\.\d+)?),(?P<lon>[+-]?\d+(?:\.\d+)?)$")


def _coordinate_origin(value: str) -> tuple[float, float] | None:
    match = GEO_ORIGIN_RE.fullmatch(value.strip())
    if not match:
        return None
    lat = float(match.group("lat"))
    lon = float(match.group("lon"))
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Invalid current location coordinates")
    return lat, lon
def geocode(address: str) -> tuple[float, float]:
    raw = str(address).strip()
    coordinate = _coordinate_origin(raw)
    if coordinate:
        return coordinate
    value = " ".join(raw.split()).strip(" ,.;")
    if not value:
        raise ValueError("Address is required")
    response = requests.get(
        GEOCODER_URL,
        params={
            "q": value,
            "fields": "items.point,items.geometry.centroid",
            "locale": "ru_RU",
            "page_size": 1,
            "key": _require_key(),
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    items = ((data.get("result") or {}).get("items") or [])
    if not items:
        raise ValueError(f"2GIS did not find location: {value}")
    return _point_from_item(items[0])


def _route_result(data) -> tuple[int, int | None]:
    if isinstance(data, list):
        routes = data
    elif isinstance(data, dict):
        if data.get("status") not in {None, "OK"}:
            raise ValueError(data.get("message") or f"2GIS route status: {data.get('status')}")
        routes = data.get("result") or []
    else:
        routes = []
    if not routes:
        raise ValueError("2GIS route was not found")
    route = min(
        (item for item in routes if item.get("total_duration") is not None),
        key=lambda item: int(item["total_duration"]),
        default=None,
    )
    if not route:
        raise ValueError("2GIS route response does not contain duration")
    seconds = int(route["total_duration"])
    distance = route.get("total_distance")
    return seconds, int(distance) if distance is not None else None


def _routing_body(origin: tuple[float, float], destination: tuple[float, float], mode: str, departure_at: datetime) -> dict:
    origin_lat, origin_lon = origin
    dest_lat, dest_lon = destination
    body = {
        "points": [
            {"type": "stop", "lon": origin_lon, "lat": origin_lat},
            {"type": "stop", "lon": dest_lon, "lat": dest_lat},
        ],
        "transport": "walking" if mode == "walking" else "driving",
        "route_mode": "fastest",
        "output": "summary",
        "locale": "ru",
    }
    if mode == "driving":
        departure_ts = int(departure_at.timestamp())
        if departure_ts > int(time.time()) + 15 * 60:
            body["utc"] = departure_ts
            body["traffic_mode"] = "statistics"
        else:
            body["traffic_mode"] = "jam"
    return body


def route(origin: tuple[float, float], destination: tuple[float, float], *, mode: str, departure_at: datetime) -> tuple[int, int | None]:
    key = _require_key()
    if mode == "transit":
        origin_lat, origin_lon = origin
        dest_lat, dest_lon = destination
        body = {
            "source": {"point": {"lat": origin_lat, "lon": origin_lon}},
            "target": {"point": {"lat": dest_lat, "lon": dest_lon}},
            "transport": PUBLIC_TRANSPORT_TYPES,
            "start_time": max(int(departure_at.timestamp()), int(time.time()) + 1),
            "enable_schedule": True,
            "locale": "ru",
            "max_result_count": 3,
        }
        response = requests.post(
            PUBLIC_TRANSPORT_URL,
            params={"key": key},
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    else:
        response = requests.post(
            ROUTING_URL,
            params={"key": key},
            json=_routing_body(origin, destination, mode, departure_at),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    response.raise_for_status()
    return _route_result(response.json())


def estimate(address_from: str, address_to: str, *, mode: str, departure_at: datetime) -> tuple[int, int | None]:
    origin = geocode(address_from)
    destination = geocode(address_to)
    seconds, distance = route(origin, destination, mode=mode, departure_at=departure_at)
    return max(1, math.ceil(seconds / 60)), distance
