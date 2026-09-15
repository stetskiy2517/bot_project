"""Deterministic airport metadata lookups used by travel imports."""

from __future__ import annotations

from functools import lru_cache
import logging
import re

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _iata_airports() -> dict[str, dict]:
    try:
        import airportsdata

        return airportsdata.load("IATA")
    except Exception:
        logger.exception("Could not load airport metadata database")
        return {}


def normalize_iata(value: object) -> str | None:
    code = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        return None
    return code


def airport_timezone(value: object) -> str | None:
    """Return the IANA timezone for an IATA airport code, if known."""
    code = normalize_iata(value)
    if not code:
        return None
    airport = _iata_airports().get(code)
    if not isinstance(airport, dict):
        return None
    timezone_name = str(airport.get("tz") or "").strip()
    return timezone_name or None
