"""Unit tests for app.engine.datacenter.distance.

These exercise the pure helpers; the DB-backed nearest-neighbor queries
are tested separately in Phase 2 against an in-memory PostGIS fixture.
"""
from __future__ import annotations

import math

import pytest

from app.engine.datacenter.distance import (
    EARTH_RADIUS_M,
    METERS_PER_FOOT,
    METERS_PER_MILE,
    centroid_of_ring,
    feet_to_meters,
    haversine_meters,
    haversine_miles,
    meters_to_miles,
    miles_to_meters,
    round_distance_mi,
)


# Known-good reference points -----------------------------------------

# (lon, lat). Sourced from authoritative geographic-name databases;
# distances cross-checked against an independent geodesy calculator.
BOSTON = (-71.0589, 42.3601)
NYC = (-74.0060, 40.7128)
LA = (-118.2437, 34.0522)
LONDON = (-0.1276, 51.5074)


# --- haversine accuracy ---------------------------------------------

def test_haversine_zero_distance():
    assert haversine_meters(*BOSTON, *BOSTON) == 0.0


def test_haversine_boston_to_nyc():
    # Real great-circle distance is ~306 km; haversine on a sphere is
    # within ~0.3% of the geodesic. Allow a 1% band.
    d_m = haversine_meters(*BOSTON, *NYC)
    assert d_m == pytest.approx(306_000, rel=0.01)
    d_mi = haversine_miles(*BOSTON, *NYC)
    assert d_mi == pytest.approx(190, rel=0.01)


def test_haversine_la_to_nyc():
    d_mi = haversine_miles(*LA, *NYC)
    # ~2446 mi, allow 1% band
    assert d_mi == pytest.approx(2446, rel=0.01)


def test_haversine_transatlantic():
    # London -> NYC: ~5570 km / ~3461 mi
    d_mi = haversine_miles(*LONDON, *NYC)
    assert d_mi == pytest.approx(3461, rel=0.01)


def test_haversine_symmetry():
    a = haversine_meters(*BOSTON, *NYC)
    b = haversine_meters(*NYC, *BOSTON)
    assert a == pytest.approx(b)


def test_haversine_short_distance_lat_only():
    # 0.001 degrees of latitude is ~111 m anywhere on Earth.
    lon, lat = -71.0, 42.0
    d_m = haversine_meters(lon, lat, lon, lat + 0.001)
    assert d_m == pytest.approx(111.0, abs=2.0)


def test_haversine_short_distance_lon_at_equator_vs_pole():
    # 0.001 degrees of longitude is ~111 m at the equator and shrinks
    # to ~0 m at the poles. Sanity check the cosine-of-latitude term.
    eq = haversine_meters(0.0, 0.0, 0.001, 0.0)
    high_lat = haversine_meters(0.0, 60.0, 0.001, 60.0)
    assert eq == pytest.approx(111.0, abs=2.0)
    # cos(60deg) = 0.5, so high-latitude span should be ~half
    assert high_lat == pytest.approx(eq * 0.5, rel=0.01)


def test_haversine_anti_meridian_safe():
    # Crossing 180deg: a small step from +179.99 to -179.99 lon should be
    # ~2.2 km at the equator, not the long way around.
    d_m = haversine_meters(179.99, 0.0, -179.99, 0.0)
    assert d_m == pytest.approx(2226, rel=0.01)


# --- unit conversions ------------------------------------------------

def test_meter_mile_roundtrip():
    for mi in (0.0, 0.1, 1.0, 5.0, 100.0):
        assert meters_to_miles(miles_to_meters(mi)) == pytest.approx(mi)


def test_constants_are_canonical():
    # NIST values for a survey/statute mile and an international foot
    assert METERS_PER_MILE == pytest.approx(1609.344, abs=1e-6)
    assert METERS_PER_FOOT == pytest.approx(0.3048, abs=1e-6)


def test_feet_to_meters():
    assert feet_to_meters(5280) == pytest.approx(1609.344, rel=1e-6)


def test_round_distance_mi():
    assert round_distance_mi(1.234567) == 1.23
    assert round_distance_mi(1.234567, ndigits=1) == 1.2
    assert round_distance_mi(0.0) == 0.0


# --- centroid_of_ring ------------------------------------------------

def test_centroid_of_unit_square():
    ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    cx, cy = centroid_of_ring(ring)
    assert cx == pytest.approx(0.5)
    assert cy == pytest.approx(0.5)


def test_centroid_handles_unclosed_ring():
    ring = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    cx, cy = centroid_of_ring(ring)
    assert cx == pytest.approx(1.0)
    assert cy == pytest.approx(1.0)


def test_centroid_empty_ring_raises():
    with pytest.raises(ValueError):
        centroid_of_ring([])


def test_earth_radius_consistent_with_haversine():
    # Quarter-circumference along a meridian should be 1/4 * 2*pi*R.
    d = haversine_meters(0.0, 0.0, 0.0, 90.0)
    assert d == pytest.approx(0.5 * math.pi * EARTH_RADIUS_M, rel=1e-6)
