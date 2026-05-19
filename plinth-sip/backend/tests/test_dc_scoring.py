"""Unit tests for app.engine.datacenter.scoring.

Pure-functional rubric tests; no DB. We pass synthetic subreport dicts
into `score_report()` and assert on the letter grade, gating issues,
and the per-category subscores.
"""
from __future__ import annotations

import pytest

from app.engine.datacenter.scoring import (
    GRADE_THRESHOLDS,
    WEIGHTS,
    composite_to_letter,
    score_report,
    substation_grade,
)


# --- weights / mapping ----------------------------------------------

def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_grade_thresholds_descend():
    cuts = [t for t, _ in GRADE_THRESHOLDS]
    assert cuts == sorted(cuts, reverse=True)


@pytest.mark.parametrize("score,letter", [
    (100, "A"), (90, "A"), (85, "A"),
    (84.99, "B"), (75, "B"), (70, "B"),
    (69.99, "C"), (60, "C"), (55, "C"),
    (54.99, "D"), (45, "D"), (40, "D"),
    (39.99, "F"), (10, "F"), (0, "F"),
])
def test_composite_to_letter(score, letter):
    assert composite_to_letter(score) == letter


# --- substation_grade convenience ------------------------------------

def test_substation_grade_a_close_high_voltage():
    assert substation_grade(0.5, 230) == "A"


def test_substation_grade_b_within_3mi():
    assert substation_grade(2.0, 115) == "B"
    assert substation_grade(3.0, 230) == "B"


def test_substation_grade_c_within_5mi():
    assert substation_grade(4.5, 115) == "C"
    assert substation_grade(5.0, 230) == "C"


def test_substation_grade_d_far_or_low_voltage():
    assert substation_grade(7.0, 230) == "D"
    assert substation_grade(0.5, 69) == "D"
    assert substation_grade(None, 230) == "D"
    assert substation_grade(0.5, None) == "D"


# --- score_report: fixtures ------------------------------------------

def _strong_report() -> dict:
    """Reference 'A-grade' parcel.

    Hyperscale-eligible acreage, industrial zoning, transmission-class
    substation half a mile away, dual feed, in PJM, low electricity
    cost, fiber and gas nearby, outside floodplain.
    """
    return {
        "grid": {
            "nearestSubstation": {"name": "Acme Sub", "operator": "PJM-Util", "maxVoltageKv": 345, "distanceMi": 0.5},
            "nearestTransmissionSubstation": {"name": "Acme Sub", "operator": "PJM-Util", "maxVoltageKv": 345, "distanceMi": 0.5},
            "substationsWithin5Mi": [{}, {}, {}],
            "nearestTransmissionLine": {"owner": "PJM-Util", "voltageKv": 230, "distanceMi": 0.3},
            "has230kvLineWithin1Mi": True,
            "transmissionCorridorsWithin5Mi": 3,
            "dualFeedFeasible": True,
            "iso": {"name": "PJM"},
        },
        "generation": {
            "nearestBaseload": {"name": "Big Plant", "fuel": "nuclear", "capacityMw": 1500, "distanceMi": 12.0},
            "capacityWithin25MiByFuel": {"nuclear": 1500, "gas": 4000, "wind": 500, "solar": 100, "coal": 0},
        },
        "power": {
            "utility": "PJM-Util",
            "industrialRateCentsPerKwh": 5.2,
            "rateTier": "Low",
        },
        "infrastructure": {
            "fiberDistanceMi": 0.4,
            "gasPipelineDistanceMi": 1.2,
            "floodZone": "X",
            "wetlandCoveragePct": 1.0,
            "acreage": 60.0,
            "acreageTier": "hyperscale",
        },
        "zoning": "industrial",
    }


def _weak_report() -> dict:
    """Reference 'D-or-F' parcel.

    Sub-edge acreage (0.5 ac), residential zoning, sub-115kV substation
    only, very high electricity cost, in coastal flood zone.
    """
    return {
        "grid": {
            "nearestSubstation": {"name": "Tiny Sub", "operator": "Utility-X", "maxVoltageKv": 69, "distanceMi": 0.8},
            "nearestTransmissionSubstation": None,
            "substationsWithin5Mi": [{}],
            "nearestTransmissionLine": {"owner": "Utility-X", "voltageKv": 69, "distanceMi": 0.5},
            "has230kvLineWithin1Mi": False,
            "transmissionCorridorsWithin5Mi": 0,
            "dualFeedFeasible": False,
            "iso": {"name": "NON-ISO"},
        },
        "generation": {
            "nearestBaseload": None,
            "capacityWithin25MiByFuel": {},
        },
        "power": {
            "utility": "Utility-X",
            "industrialRateCentsPerKwh": 18.5,
            "rateTier": "Very High",
        },
        "infrastructure": {
            "fiberDistanceMi": 14.0,
            "gasPipelineDistanceMi": 22.0,
            "floodZone": "VE",
            "wetlandCoveragePct": 30.0,
            "acreage": 0.5,
            "acreageTier": "edge",
        },
        "zoning": "residential",
    }


def _mid_report() -> dict:
    """Reference 'B/C' parcel.

    Colo-eligible acreage, commercial zoning, 115kV substation 3.5 mi
    away, no dual feed, in MISO, medium electricity cost, outside
    flood, modest fiber distance.
    """
    return {
        "grid": {
            "nearestSubstation": {"name": "Mid Sub", "operator": "MISO-Util", "maxVoltageKv": 161, "distanceMi": 3.5},
            "nearestTransmissionSubstation": {"name": "Mid Sub", "operator": "MISO-Util", "maxVoltageKv": 161, "distanceMi": 3.5},
            "substationsWithin5Mi": [{}],
            "nearestTransmissionLine": {"owner": "MISO-Util", "voltageKv": 161, "distanceMi": 1.8},
            "has230kvLineWithin1Mi": False,
            "transmissionCorridorsWithin5Mi": 1,
            "dualFeedFeasible": False,
            "iso": {"name": "MISO"},
        },
        "generation": {
            "nearestBaseload": {"name": "CC Plant", "fuel": "gas", "capacityMw": 600, "distanceMi": 38.0},
            "capacityWithin25MiByFuel": {"gas": 800, "wind": 600, "solar": 100},
        },
        "power": {
            "utility": "MISO-Util",
            "industrialRateCentsPerKwh": 7.5,
            "rateTier": "Medium",
        },
        "infrastructure": {
            "fiberDistanceMi": 4.0,
            "gasPipelineDistanceMi": 6.0,
            "floodZone": "X",
            "wetlandCoveragePct": 8.0,
            "acreage": 12.0,
            "acreageTier": "colo",
        },
        "zoning": "commercial",
    }


# --- score_report: end-to-end --------------------------------------

def test_strong_parcel_is_a_or_b_grade():
    r = score_report(_strong_report())
    assert r.letter in ("A", "B"), f"strong parcel got {r.letter}; rationale={r.rationale}"
    assert r.gating_issues == []


def test_strong_parcel_composite_above_75():
    r = score_report(_strong_report())
    assert r.composite >= 75.0


def test_weak_parcel_is_f_with_gating():
    r = score_report(_weak_report())
    assert r.letter == "F"
    # Three independent gates fire on this parcel: sub-1ac, no >=115kV
    # substation, and Zone VE.
    assert any("V" in g or "VE" in g for g in r.gating_issues)
    assert any("acre" in g.lower() for g in r.gating_issues)


def test_mid_parcel_lands_in_b_c_d_band():
    r = score_report(_mid_report())
    assert r.letter in ("B", "C", "D"), f"mid got {r.letter}"
    assert r.composite >= 50.0
    assert r.composite < 85.0


# --- F-gating in isolation ------------------------------------------

def test_subacre_parcel_is_f_gated():
    rep = _strong_report()
    rep["infrastructure"]["acreage"] = 0.4
    r = score_report(rep)
    assert r.letter == "F"
    assert any("acre" in g.lower() for g in r.gating_issues)


def test_floodway_is_f_gated():
    rep = _strong_report()
    rep["infrastructure"]["floodZone"] = "FLOODWAY"
    r = score_report(rep)
    assert r.letter == "F"


def test_zone_ve_is_f_gated():
    rep = _strong_report()
    rep["infrastructure"]["floodZone"] = "VE"
    r = score_report(rep)
    assert r.letter == "F"


def test_no_115kv_substation_within_25mi_is_f_gated():
    rep = _strong_report()
    rep["grid"]["nearestTransmissionSubstation"] = {
        "name": "Far Sub", "operator": "X", "maxVoltageKv": 230, "distanceMi": 32.0,
    }
    r = score_report(rep)
    assert r.letter == "F"
    assert any("25 mi" in g for g in r.gating_issues)


# --- subscore behavior ----------------------------------------------

def test_subscores_present_and_in_range():
    r = score_report(_strong_report())
    for cat in ("grid", "power_cost", "infrastructure", "land", "generation", "iso"):
        assert cat in r.subscores
        assert 0 <= r.subscores[cat] <= 100


def test_dual_feed_meaningfully_increases_grid_subscore():
    rep_with = _strong_report()
    rep_without = _strong_report()
    rep_without["grid"]["dualFeedFeasible"] = False
    s_with = score_report(rep_with).subscores["grid"]
    s_without = score_report(rep_without).subscores["grid"]
    assert s_with > s_without


def test_unknown_rate_does_not_zero_power_cost_score():
    """Unknowns should be treated as middling, not as worst-case."""
    rep = _strong_report()
    rep["power"]["rateTier"] = None
    rep["power"]["industrialRateCentsPerKwh"] = None
    r = score_report(rep)
    assert 30 <= r.subscores["power_cost"] <= 80


def test_residential_zoning_drops_land_score():
    rep_ind = _strong_report()
    rep_res = _strong_report()
    rep_res["zoning"] = "residential"
    s_ind = score_report(rep_ind).subscores["land"]
    s_res = score_report(rep_res).subscores["land"]
    assert s_ind > s_res


def test_non_iso_lower_iso_subscore_than_in_iso():
    rep_pjm = _strong_report()
    rep_non = _strong_report()
    rep_non["grid"]["iso"]["name"] = "NON-ISO"
    assert score_report(rep_pjm).subscores["iso"] > score_report(rep_non).subscores["iso"]


# --- rationale shape ------------------------------------------------

def test_rationale_string_present_for_all_grades():
    for builder in (_strong_report, _mid_report, _weak_report):
        r = score_report(builder())
        assert isinstance(r.rationale, str) and len(r.rationale) > 5
