"""Resolve an IANA timezone from a human-readable place without domain-specific rules."""

from __future__ import annotations

from functools import lru_cache
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import NAVIGATION_PROVIDER
from integrations.navigation_2gis import configured as dgis_configured, geocode as dgis_geocode
from integrations.navigation_ors import configured as ors_configured, geocode as ors_geocode

try:
    from tzfpy import get_tz
except ImportError:  # Optional safety: file analysis must not break app startup.
    get_tz = None

logger = logging.getLogger(__name__)


def _clean_location(value: object) -> str:
    return " ".join(str(value or "").split()).strip(" ,.;")[:500]


def _valid_zone(value: object) -> str | None:
    name = str(value or "").strip()
    if not name:
        return None
    try:
        return str(ZoneInfo(name).key)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _provider_order() -> tuple[str, ...]:
    preferred = str(NAVIGATION_PROVIDER or "").strip().lower()
    if preferred in {"ors", "openrouteservice"}:
        first = "ors"
    elif preferred == "2gis":
        first = "2gis"
    else:
        first = ""
    result: list[str] = []
    for provider in (first, "ors", "2gis"):
        if provider and provider not in result:
            result.append(provider)
    return tuple(result)


def _geocode(provider: str, location: str) -> tuple[float, float] | None:
    """Return (longitude, latitude) for a configured provider."""
    if provider == "ors":
        if not ors_configured():
            return None
        longitude, latitude = ors_geocode(location)
        return float(longitude), float(latitude)
    if provider == "2gis":
        if not dgis_configured():
            return None
        latitude, longitude = dgis_geocode(location)
        return float(longitude), float(latitude)
    return None


@lru_cache(maxsize=512)
def resolve_location_timezone(location: str) -> dict | None:
    """Resolve place -> coordinates -> IANA timezone using generic geo infrastructure.

    Returns None when no geocoder is configured, the place cannot be geocoded, or the
    offline timezone dataset is unavailable. Failures are intentionally isolated from
    the rest of the assistant.
    """
    value = _clean_location(location)
    if not value or get_tz is None:
        return None

    for provider in _provider_order():
        try:
            point = _geocode(provider, value)
            if point is None:
                continue
            longitude, latitude = point
            if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
                continue
            timezone_name = _valid_zone(get_tz(longitude, latitude))
            if timezone_name:
                return {
                    "timezone": timezone_name,
                    "longitude": longitude,
                    "latitude": latitude,
                    "provider": provider,
                }
        except Exception:
            logger.debug("Location timezone lookup failed via %s", provider, exc_info=True)
    return None
