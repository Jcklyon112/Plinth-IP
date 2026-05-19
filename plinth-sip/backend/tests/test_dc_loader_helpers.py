"""Unit tests for the loader-side pure helpers.

`clean_int` / `clean_float` / `clean_str` swallow HIFLD's null sentinels;
`normalize_fuel` produces the analyzer-facing fuel vocabulary. Both are
hot paths for the data quality of the feasibility report and worth
guarding directly.
"""
from __future__ import annotations

import pytest

from data.grid.loaders._arcgis import clean_float, clean_int, clean_str
from data.grid.loaders.hifld_power_plants import normalize_fuel


# --- clean_int -------------------------------------------------------

@pytest.mark.parametrize("v,expected", [
    (None, None),
    ("", None),
    (-999999, None),
    ("-999999", None),
    ("NOT AVAILABLE", None),
    ("not available", None),
    ("  NA  ", None),
    ("UNKNOWN", None),
    (115, 115),
    ("115", 115),
    ("115.0", 115),
    ("345.5", 345),       # truncates toward zero via int(float())
    (0, 0),
])
def test_clean_int(v, expected):
    assert clean_int(v) == expected


def test_clean_int_garbage_string():
    assert clean_int("hello") is None


# --- clean_float -----------------------------------------------------

@pytest.mark.parametrize("v,expected", [
    (None, None),
    ("", None),
    (-999999, None),
    ("NA", None),
    (3.14, pytest.approx(3.14)),
    ("3.14", pytest.approx(3.14)),
    (115, pytest.approx(115.0)),
])
def test_clean_float(v, expected):
    assert clean_float(v) == expected


# --- clean_str -------------------------------------------------------

@pytest.mark.parametrize("v,expected", [
    (None, None),
    ("", None),
    ("   ", None),
    ("NOT AVAILABLE", None),
    ("Hello", "Hello"),
    ("  Padded  ", "Padded"),
    (123, "123"),
])
def test_clean_str(v, expected):
    assert clean_str(v) == expected


# --- normalize_fuel --------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("NUC", "nuclear"),
    ("nuclear", "nuclear"),
    ("NG", "gas"),
    ("Natural Gas", "gas"),
    ("LNG", "gas"),
    ("COAL", "coal"),
    ("BIT", "coal"),
    ("LIG", "coal"),
    ("WND", "wind"),
    ("Wind", "wind"),
    ("WON", "wind"),     # onshore
    ("WOF", "wind"),     # offshore
    ("SUN", "solar"),
    ("Solar PV", "solar"),
    ("WAT", "hydro"),
    ("Hydro", "hydro"),
    ("HPS", "hydro"),    # pumped storage
    ("OIL", "oil"),
    ("DFO", "oil"),
    ("Petroleum", "oil"),
    ("BIO", "biomass"),
    ("Biomass", "biomass"),
    ("MSW", "biomass"),
    ("LFG", "biomass"),
    ("GEO", "geothermal"),
    ("Geothermal", "geothermal"),
    ("BAT", "battery"),
    ("Battery storage", "battery"),
    ("ES", "battery"),
    ("Compressed air storage", "battery"),  # storage substring fallback
    ("ALIEN_FUEL", "other"),
    (None, None),
    ("", None),
    ("   ", None),
])
def test_normalize_fuel(raw, expected):
    assert normalize_fuel(raw) == expected
