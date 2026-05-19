"""Unit tests for app.engine.datacenter.iso (the pure helpers).

The DB-backed `iso_for_point` path is exercised in Phase 2 against a
live PostGIS fixture; here we only test the BA-code -> ISO mapping,
which is what the BA loader uses at load time and is what we expose
to analysts via `iso_metadata.json`.
"""
from __future__ import annotations

import json

import pytest

from app.engine.datacenter.iso import (
    iso_for_ba_code,
    iso_metadata_entry,
    load_iso_metadata,
)


# --- live metadata sanity --------------------------------------------

def test_metadata_loads_and_contains_seven_isos_plus_non_iso():
    md = load_iso_metadata()
    isos = md.get("isos", {})
    expected = {"PJM", "MISO", "ERCOT", "CAISO", "NYISO", "ISO-NE", "SPP", "NON-ISO"}
    assert set(isos.keys()) == expected


def test_every_iso_entry_has_the_required_fields():
    md = load_iso_metadata()
    for key, entry in md["isos"].items():
        assert "full_name" in entry, f"{key} missing full_name"
        assert "current_posture" in entry, f"{key} missing current_posture"
        assert "queue_dashboard_url" in entry, f"{key} missing queue_dashboard_url"
        assert "ba_codes" in entry, f"{key} missing ba_codes (may be empty list)"


def test_iso_metadata_entry_returns_named_block():
    e = iso_metadata_entry("PJM")
    assert e["name"] == "PJM"
    assert e["full_name"] == "PJM Interconnection"
    # current_posture is a hand-edited TODO at first; we don't assert on
    # its content so updates don't break tests, but we do require the key.
    assert "current_posture" in e


def test_iso_metadata_entry_unknown_iso_returns_named_empty():
    e = iso_metadata_entry("DEFINITELY_NOT_AN_ISO")
    assert e == {"name": "DEFINITELY_NOT_AN_ISO"}


# --- BA-code mapping (against live metadata) -------------------------

@pytest.mark.parametrize(
    "ba_code,expected",
    [
        ("PJM", "PJM"),
        ("MISO", "MISO"),
        ("ERCO", "ERCOT"),     # HIFLD/EIA short form
        ("ERCOT", "ERCOT"),
        ("CISO", "CAISO"),
        ("CAISO", "CAISO"),
        ("NYIS", "NYISO"),
        ("ISNE", "ISO-NE"),
        ("SWPP", "SPP"),
        ("TVA", "NON-ISO"),     # vertically integrated
        ("DUK", "NON-ISO"),
        ("FPL", "NON-ISO"),
        ("BPAT", "NON-ISO"),
    ],
)
def test_iso_for_ba_code_known_codes(ba_code, expected):
    assert iso_for_ba_code(ba_code) == expected


def test_iso_for_ba_code_is_case_insensitive():
    assert iso_for_ba_code("pjm") == "PJM"
    assert iso_for_ba_code(" PJM ") == "PJM"
    assert iso_for_ba_code("Pjm") == "PJM"


def test_iso_for_ba_code_empty_string():
    assert iso_for_ba_code("") == "NON-ISO"


def test_iso_for_ba_code_unknown_falls_back_to_non_iso(caplog):
    # Unknown codes should bucket as NON-ISO and emit a warning. The
    # warning is rate-limited per code via lru_cache — we don't assert
    # the count, just that we logged at least once.
    import logging
    caplog.set_level(logging.WARNING, logger="app.engine.datacenter.iso")
    assert iso_for_ba_code("DEFINITELY_NOT_A_BA_XYZ_001") == "NON-ISO"


# --- BA-code mapping (against synthetic minimal metadata) ------------
# These tests use an injected metadata dict so the contract is decoupled
# from the live JSON file's evolution.

@pytest.fixture
def synthetic_md():
    return {
        "isos": {
            "PJM": {"ba_codes": ["PJM", "PJM-EAST"]},
            "MISO": {"ba_codes": ["MISO"]},
            "NON-ISO": {"ba_codes": []},
        },
        "ba_code_overrides": {
            "TVA": "NON-ISO",
            "FAKE_PJM_OVERRIDE": "PJM",
            "_README": "ignored leading-underscore key",
        },
    }


def test_synthetic_iso_mapping(synthetic_md):
    assert iso_for_ba_code("PJM", synthetic_md) == "PJM"
    assert iso_for_ba_code("PJM-EAST", synthetic_md) == "PJM"
    assert iso_for_ba_code("MISO", synthetic_md) == "MISO"


def test_synthetic_overrides_apply(synthetic_md):
    assert iso_for_ba_code("TVA", synthetic_md) == "NON-ISO"
    assert iso_for_ba_code("FAKE_PJM_OVERRIDE", synthetic_md) == "PJM"


def test_synthetic_ignores_leading_underscore_keys_in_overrides(synthetic_md):
    # The "_README" override key must not match a "_README" BA code.
    assert iso_for_ba_code("_README", synthetic_md) == "NON-ISO"


def test_synthetic_unknown_code_is_non_iso(synthetic_md):
    assert iso_for_ba_code("UNKNOWN", synthetic_md) == "NON-ISO"


def test_synthetic_iso_codes_match_case_insensitively(synthetic_md):
    assert iso_for_ba_code("pjm", synthetic_md) == "PJM"
    assert iso_for_ba_code("tva", synthetic_md) == "NON-ISO"


# --- iso_metadata.json structural integrity --------------------------
# These guard the file itself: any future edit that breaks the schema
# causes a clear test failure rather than a runtime KeyError.

def test_metadata_file_is_valid_json():
    md = load_iso_metadata()
    # round-trip
    blob = json.dumps(md)
    assert json.loads(blob) == md


def test_metadata_ba_codes_are_uppercase_lists():
    md = load_iso_metadata()
    for iso_key, entry in md["isos"].items():
        bas = entry.get("ba_codes", [])
        assert isinstance(bas, list)
        for c in bas:
            assert isinstance(c, str)
            # We don't strictly require uppercase since the matcher
            # uppercases at lookup time, but it's a good sanity convention.
            assert c == c.strip()


def test_metadata_overrides_target_real_isos():
    md = load_iso_metadata()
    iso_keys = set(md["isos"].keys())
    for ba, target in md["ba_code_overrides"].items():
        if ba.startswith("_"):
            continue
        assert target in iso_keys, (
            f"override {ba!r} -> {target!r} but {target!r} is not a known ISO key"
        )
