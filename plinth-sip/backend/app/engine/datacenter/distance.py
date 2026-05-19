"""Pure distance helpers.

Two layers:

* Pure functions (haversine, conversions). No DB, no Shapely. These get
  unit-tested directly in `backend/tests/test_dc_distance.py`.
* DB helpers (e.g. `nearest_substation`) live next to the analyzer in
  later phases and use PostGIS `<->` KNN operators on geography casts.

Why not Shapely for distances? Shapely operates in the input CRS's
units. Our geometries are EPSG:4326 (degrees), so Shapely distances
would be in degrees - meaningless for the report. We do all distance
math in meters via either haversine (point-to-point) or PostGIS
`ST_Distance(geography, geography)` (point-to-line).
"""
from __future__ import annotations

import math
from typing import Tuple

# Mean Earth radius in meters (WGS84 sphere). Good enough for desk-grade
# proximity checks; the spec quotes distances in 0.1-mile precision so
# the ~0.3% haversine error vs Vincenty is well below report resolution.
EARTH_RADIUS_M = 6_371_008.8

METERS_PER_MILE = 1609.344
METERS_PER_FOOT = 0.3048


def haversine_meters(
    lon1: float, lat1: float, lon2: float, lat2: float
) -> float:
    """Great-circle distance between two WGS84 points, in meters."""
    rlat1 = math.radians(lat1)
    rlat2 = math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.asin(min(1.0, math.sqrt(a)))
    return EARTH_RADIUS_M * c


def meters_to_miles(m: float) -> float:
    return m / METERS_PER_MILE


def miles_to_meters(mi: float) -> float:
    return mi * METERS_PER_MILE


def feet_to_meters(ft: float) -> float:
    return ft * METERS_PER_FOOT


def haversine_miles(
    lon1: float, lat1: float, lon2: float, lat2: float
) -> float:
    """Great-circle distance in miles."""
    return meters_to_miles(haversine_meters(lon1, lat1, lon2, lat2))


def round_distance_mi(mi: float, *, ndigits: int = 2) -> float:
    """Round to a sensible report precision (0.01 mi ~= 53 ft)."""
    return round(mi, ndigits)


# Point/centroid utilities ---------------------------------------------

def centroid_of_ring(ring: list[Tuple[float, float]]) -> Tuple[float, float]:
    """Centroid of a closed ring of (lon, lat) tuples (simple average).

    Used as a fallback when we don't have access to PostGIS / Shapely
    and the parcel is small enough that a flat average is indistinguishable
    from a true centroid at report precision.
    """
    if not ring:
        raise ValueError("empty ring")
    # Drop duplicate close-of-ring point if present.
    pts = ring[:-1] if ring[0] == ring[-1] and len(ring) > 1 else ring
    n = len(pts)
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    return (sx / n, sy / n)
