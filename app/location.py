"""Hangout location helpers (display string + optional Places structure).

Product model: docs/location-and-carpool.md §1.
`hangouts.location` is the human display line; place_id / lat / lng are optional.
"""

from __future__ import annotations

from typing import Any

from app.models import Hangout

# Matches Places API request bound in app/routers/api.py (store shorter).
_PLACE_ID_MAX = 512
_LAT_MIN, _LAT_MAX = -90.0, 90.0
_LNG_MIN, _LNG_MAX = -180.0, 180.0


def location_display(hangout: Hangout | Any) -> str | None:
    """Human Where: line for SMS, lists, and detail headers."""
    raw = getattr(hangout, "location", None)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _parse_coord(value: float | int | str | None, *, lo: float, hi: float) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    if number < lo or number > hi:
        return None
    return number


def normalize_place_id(place_id: str | None) -> str | None:
    if place_id is None:
        return None
    text = str(place_id).strip()
    if not text:
        return None
    return text[:_PLACE_ID_MAX]


def apply_hangout_location(
    hangout: Hangout,
    *,
    location: str | None,
    location_place_id: str | None = None,
    location_latitude: float | int | str | None = None,
    location_longitude: float | int | str | None = None,
) -> None:
    """Set display location and optional structured Places fields.

    Empty display clears the whole location (including structure).
    Structured fields without a matching Places selection should be omitted
    (text-only location); pass empty place_id/lat/lng to clear structure while
    keeping the display string.
    """
    display = (location or "").strip() or None
    hangout.location = display
    if display is None:
        hangout.location_place_id = None
        hangout.location_latitude = None
        hangout.location_longitude = None
        return

    hangout.location_place_id = normalize_place_id(location_place_id)
    hangout.location_latitude = _parse_coord(
        location_latitude, lo=_LAT_MIN, hi=_LAT_MAX
    )
    hangout.location_longitude = _parse_coord(
        location_longitude, lo=_LNG_MIN, hi=_LNG_MAX
    )
    # Coords are only useful as a pair for maps later; drop incomplete pairs.
    if hangout.location_latitude is None or hangout.location_longitude is None:
        hangout.location_latitude = None
        hangout.location_longitude = None
