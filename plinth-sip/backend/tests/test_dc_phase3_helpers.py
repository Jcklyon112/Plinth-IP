"""Phase 3 unit tests: pure helpers in the new loaders + NWI module.

We exercise rate computation, EIA column matching, KML parsing, and
NWI wetland-coverage math. The DB-touching loader bodies are
integration-tested against a live PostGIS in a follow-up.
"""
from __future__ import annotations

import pytest

from app.engine.datacenter.nwi import coverage_pct_from_features
from data.grid.loaders.eia_form861_rates import (
    _match_columns,
    compute_rate_cents_per_kwh,
    infer_year_from_filename,
)
from data.grid.loaders._kml import parse_kml_bytes


# --- EIA: rate math --------------------------------------------------

@pytest.mark.parametrize("rev_thousand,sales_mwh,expected", [
    # 5,000 thousand = $5M revenue, 100,000 MWh sales -> 5c/kWh
    (5_000.0, 100_000.0, 5.0),
    # 1,000 thousand = $1M, 10,000 MWh -> 10c/kWh
    (1_000.0, 10_000.0, 10.0),
    # zero / negative sales -> None
    (5_000.0, 0.0, None),
    (5_000.0, -1.0, None),
    (None, 10_000.0, None),
    (5_000.0, None, None),
])
def test_compute_rate_cents_per_kwh(rev_thousand, sales_mwh, expected):
    got = compute_rate_cents_per_kwh(rev_thousand, sales_mwh)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected, rel=1e-9)


# --- EIA: year inference --------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Sales_Ult_Cust_2023.xlsx", 2023),
    ("Sales_Ult_Cust_2007.xlsx", 2007),
    ("eia861_2024.csv", 2024),
    ("file_with_no_year.csv", None),
    ("1234567890_not_a_year.xlsx", None),    # 9-digit number, no 4-digit year-shaped substring
    ("Sales 1999 archive.xlsx", 1999),
])
def test_infer_year_from_filename(name, expected):
    assert infer_year_from_filename(name) == expected


# --- EIA: column matcher ---------------------------------------------

def test_match_columns_finds_industrial_columns():
    headers = [
        "Utility Number",
        "Utility Name",
        "State",
        "Data Year",
        "Industrial Revenues (Thousand Dollars)",
        "Industrial Sales (Megawatthours)",
        "Industrial Customers (Count)",
    ]
    cols = _match_columns(headers)
    assert cols["utility_id_eia"] == 0
    assert cols["utility_name"] == 1
    assert cols["state"] == 2
    assert cols["year"] == 3
    assert cols["revenue_thousand_usd"] == 4
    assert cols["sales_mwh"] == 5
    assert cols["customers"] == 6


def test_match_columns_tolerates_messy_headers():
    headers = [
        "  Utility ID EIA  ",
        "Utility   NAME",
        "STATE",
        "YEAR",
        "Industrial revenue thousands",
        "MWh industrial sales",
        "Industrial customer count",
    ]
    cols = _match_columns(headers)
    # All seven keys must resolve regardless of whitespace/casing.
    for k in ("utility_id_eia", "utility_name", "state", "year",
              "revenue_thousand_usd", "sales_mwh", "customers"):
        assert k in cols, f"missing {k} in {cols}"


def test_match_columns_skips_non_industrial_blocks():
    """Residential / commercial blocks must NOT be picked up by the
    industrial-keyed patterns."""
    headers = [
        "Utility Number", "Utility Name",
        "Residential Revenue (Thousand Dollars)",
        "Residential Sales (Megawatthours)",
        "Industrial Revenue (Thousand Dollars)",
        "Industrial Sales (Megawatthours)",
    ]
    cols = _match_columns(headers)
    # Industrial columns must point at the *industrial* indices (4, 5),
    # not the residential block (2, 3).
    assert cols["revenue_thousand_usd"] == 4
    assert cols["sales_mwh"] == 5


# --- KML parser ------------------------------------------------------

_KML_LINESTRING = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Lumen US East</name>
      <description>Long-haul fiber spine</description>
      <LineString>
        <coordinates>
          -77.0,38.9,0
          -76.9,39.0,0
          -76.8,39.1,0
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""

_KML_NO_NAMESPACE = b"""<?xml version="1.0"?>
<kml>
  <Document>
    <Placemark>
      <name>Older export</name>
      <LineString>
        <coordinates>-100,40 -101,41</coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""

_KML_MULTIGEOMETRY = b"""<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Two-segment route</name>
      <MultiGeometry>
        <LineString><coordinates>-77,38 -76,39</coordinates></LineString>
        <LineString><coordinates>-75,40 -74,41</coordinates></LineString>
      </MultiGeometry>
    </Placemark>
  </Document>
</kml>
"""

_KML_POINT = b"""<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>POP</name>
      <Point><coordinates>-77.0,38.9,0</coordinates></Point>
    </Placemark>
  </Document>
</kml>
"""


def test_parse_kml_linestring():
    feats = parse_kml_bytes(_KML_LINESTRING)
    assert len(feats) == 1
    f = feats[0]
    assert f["geometry"]["type"] == "LineString"
    assert len(f["geometry"]["coordinates"]) == 3
    # Confirm coords are [lon, lat] (no altitude carried through)
    assert f["geometry"]["coordinates"][0] == [-77.0, 38.9]
    assert f["properties"]["name"] == "Lumen US East"
    assert "fiber" in f["properties"]["description"]


def test_parse_kml_strips_namespace():
    feats = parse_kml_bytes(_KML_NO_NAMESPACE)
    assert len(feats) == 1
    assert feats[0]["geometry"]["type"] == "LineString"


def test_parse_kml_multigeometry_promotes_to_multilinestring():
    feats = parse_kml_bytes(_KML_MULTIGEOMETRY)
    assert len(feats) == 1
    g = feats[0]["geometry"]
    assert g["type"] == "MultiLineString"
    assert len(g["coordinates"]) == 2


def test_parse_kml_point():
    feats = parse_kml_bytes(_KML_POINT)
    assert len(feats) == 1
    assert feats[0]["geometry"]["type"] == "Point"
    assert feats[0]["geometry"]["coordinates"] == [-77.0, 38.9]


def test_parse_kml_garbage_returns_empty_list():
    assert parse_kml_bytes(b"<not-xml>") == []
    assert parse_kml_bytes(b"") == []


# --- NWI coverage math ----------------------------------------------

# Parcel: a 0.001 deg x 0.001 deg square at the equator (~111 m x 111 m)
PARCEL_WKT = "POLYGON((0 0, 0.001 0, 0.001 0.001, 0 0.001, 0 0))"


def _wetland_feature(coords: list[list[list[float]]]) -> dict:
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": coords}, "properties": {}}


def test_coverage_zero_when_no_features():
    assert coverage_pct_from_features(PARCEL_WKT, []) == 0.0


def test_coverage_zero_when_features_outside_parcel():
    far_away = _wetland_feature([[[10, 10], [10.1, 10], [10.1, 10.1], [10, 10.1], [10, 10]]])
    assert coverage_pct_from_features(PARCEL_WKT, [far_away]) == 0.0


def test_coverage_full_when_feature_covers_parcel():
    # Wetland polygon strictly larger than the parcel
    big = _wetland_feature([[[-1, -1], [2, -1], [2, 2], [-1, 2], [-1, -1]]])
    assert coverage_pct_from_features(PARCEL_WKT, [big]) == 100.0


def test_coverage_partial_when_feature_overlaps_half():
    # Wetland covers exactly the eastern half of the parcel (lon 0.0005-0.001)
    half = _wetland_feature(
        [[[0.0005, 0], [0.001, 0], [0.001, 0.001], [0.0005, 0.001], [0.0005, 0]]]
    )
    cov = coverage_pct_from_features(PARCEL_WKT, [half])
    assert cov == pytest.approx(50.0, abs=0.5)


def test_coverage_combined_features_unioned_not_double_counted():
    # Two features overlapping each other but covering 50% of parcel together
    halfA = _wetland_feature(
        [[[0.0005, 0], [0.001, 0], [0.001, 0.001], [0.0005, 0.001], [0.0005, 0]]]
    )
    halfB = _wetland_feature(
        # extends from 0.0006 to 0.001 -> subset of halfA, must not double-count
        [[[0.0006, 0], [0.001, 0], [0.001, 0.001], [0.0006, 0.001], [0.0006, 0]]]
    )
    cov = coverage_pct_from_features(PARCEL_WKT, [halfA, halfB])
    # Unioned coverage is the same as halfA alone: 50%
    assert cov == pytest.approx(50.0, abs=0.5)


def test_coverage_invalid_parcel_returns_none():
    # zero-area parcel (point) -> None, since coverage% is undefined
    assert coverage_pct_from_features("POINT(0 0)", []) is None
    # garbage WKT -> None
    assert coverage_pct_from_features("not-wkt", []) is None
