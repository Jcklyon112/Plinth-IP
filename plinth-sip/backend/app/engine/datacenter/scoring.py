"""A/B/C/D/F scoring rubric for data-center feasibility.

Composite is a weighted sum of six 0-100 subscores; the letter grade is
mapped from the composite, with hard-block "F-gates" that force F
regardless of the composite (e.g., parcel <1 acre, no >=115kV substation
within 25 mi, FEMA Zone V).

This module is **pure** - no DB, no I/O. It takes the assembled
analyzer subreport dict and returns
`{score, rationale, gating_issues, subscores: {...}}`. The analyzer
plugs the result back into the spec'd JSON shape.

Tuning happens via the `WEIGHTS` constant. Tests live in
`backend/tests/test_dc_scoring.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# --- weights (must sum to 1.0) ----------------------------------------

WEIGHTS = {
    "grid": 0.40,
    "power_cost": 0.15,
    "infrastructure": 0.15,
    "land": 0.15,
    "generation": 0.10,
    "iso": 0.05,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "WEIGHTS must sum to 1.0"


# --- letter mapping ---------------------------------------------------

GRADE_THRESHOLDS = [
    (85, "A"),
    (70, "B"),
    (55, "C"),
    (40, "D"),
]


def composite_to_letter(score: float) -> str:
    for threshold, letter in GRADE_THRESHOLDS:
        if score >= threshold:
            return letter
    return "F"


# --- subscore helpers -------------------------------------------------

def _grid_subscore(grid: dict) -> tuple[float, list[str]]:
    notes: list[str] = []

    # Substation proximity is the dominant grid signal.
    near_tx_sub = grid.get("nearestTransmissionSubstation")
    if near_tx_sub is None:
        sub_score = 20.0
        notes.append("No transmission-class (>=115kV) substation found.")
    else:
        d = near_tx_sub.get("distanceMi", 999)
        v = near_tx_sub.get("maxVoltageKv") or 0
        if d < 1.0 and v >= 115:
            sub_score = 100.0
        elif d <= 3.0 and v >= 115:
            sub_score = 80.0
        elif d <= 5.0 and v >= 115:
            sub_score = 60.0
        else:
            sub_score = 35.0
            notes.append(f"Nearest >=115kV substation is {d} mi away.")

    # Transmission line proximity (greenfield-substation potential).
    near_tx = grid.get("nearestTransmissionLine") or {}
    d_line = near_tx.get("distanceMi")
    v_line = near_tx.get("voltageKv") or 0
    if d_line is None:
        line_score = 30.0
    elif d_line <= 0.5 and v_line >= 230:
        line_score = 100.0
    elif d_line <= 1.0 and v_line >= 230:
        line_score = 90.0
    elif d_line <= 1.0 and v_line >= 115:
        line_score = 75.0
    elif d_line <= 3.0 and v_line >= 115:
        line_score = 60.0
    else:
        line_score = 30.0

    dual_feed = bool(grid.get("dualFeedFeasible"))
    dual_score = 100.0 if dual_feed else 50.0
    if not dual_feed:
        notes.append("Dual-feed not detected within 5 mi.")

    # Weighted within grid: sub 60%, line 25%, dual 15%
    grid_total = 0.60 * sub_score + 0.25 * line_score + 0.15 * dual_score
    return grid_total, notes


def _power_cost_subscore(power: dict) -> tuple[float, list[str]]:
    tier = power.get("rateTier")
    if tier == "Low":
        return 100.0, []
    if tier == "Medium":
        return 75.0, []
    if tier == "High":
        return 45.0, ["High industrial electricity tariff."]
    if tier == "Very High":
        return 20.0, ["Very high industrial electricity tariff (>14 c/kWh)."]
    return 60.0, ["Utility / industrial rate unknown."]


def _infra_subscore(infra: dict) -> tuple[float, list[str]]:
    notes: list[str] = []

    fiber = infra.get("fiberDistanceMi")
    if fiber is None:
        fiber_s = 60.0
        notes.append("Fiber data not loaded; treat as unknown.")
    elif fiber <= 1.0:
        fiber_s = 100.0
    elif fiber <= 5.0:
        fiber_s = 70.0
    elif fiber <= 10.0:
        fiber_s = 40.0
    else:
        fiber_s = 20.0
        notes.append(f"Nearest fiber is {fiber} mi away.")

    gas = infra.get("gasPipelineDistanceMi")
    if gas is None:
        gas_s = 60.0
    elif gas <= 5.0:
        gas_s = 100.0
    elif gas <= 10.0:
        gas_s = 60.0
    else:
        gas_s = 30.0

    flood = (infra.get("floodZone") or "").upper()
    if flood in ("V", "VE"):
        flood_s = 0.0
        notes.append(f"Parcel in FEMA flood zone {flood} (coastal high-hazard).")
    elif flood in ("A", "AE", "AH", "AO"):
        flood_s = 30.0
        notes.append(f"Parcel in FEMA flood zone {flood} (1% annual chance).")
    elif flood == "":
        flood_s = 60.0
        notes.append("FEMA flood zone unknown.")
    else:
        flood_s = 100.0

    wet = infra.get("wetlandCoveragePct")
    if wet is None:
        wet_s = 60.0
    elif wet < 5.0:
        wet_s = 100.0
    elif wet < 25.0:
        wet_s = 60.0
    elif wet < 50.0:
        wet_s = 30.0
    else:
        wet_s = 0.0
        notes.append("Wetlands cover >50% of parcel.")

    # Within infrastructure: fiber 40%, gas 20%, flood 20%, wetlands 20%
    total = 0.40 * fiber_s + 0.20 * gas_s + 0.20 * flood_s + 0.20 * wet_s
    return total, notes


def _land_subscore(infra: dict, zoning: str) -> tuple[float, list[str]]:
    notes: list[str] = []

    acres = infra.get("acreage")
    if acres is None:
        acreage_s = 30.0
        notes.append("Parcel acreage unknown.")
    elif acres < 1.0:
        acreage_s = 0.0
        notes.append(f"Parcel is only {acres:.2f} ac (sub-edge).")
    elif acres < 5.0:
        acreage_s = 50.0
    elif acres < 25.0:
        acreage_s = 80.0
    elif acres < 100.0:
        acreage_s = 95.0
    else:
        acreage_s = 100.0

    z = (zoning or "unknown").lower()
    if z == "industrial":
        zoning_s = 100.0
    elif z == "heavy_commercial":
        zoning_s = 80.0
    elif z == "commercial":
        zoning_s = 65.0
    elif z == "agricultural":
        zoning_s = 55.0
        notes.append("Agricultural zoning typically requires rezone for DC use.")
    elif z == "mixed":
        zoning_s = 50.0
    elif z == "residential":
        zoning_s = 25.0
        notes.append("Residential zoning is a hard barrier in most jurisdictions.")
    else:
        zoning_s = 50.0
        notes.append("Zoning compatibility unknown.")

    # Within land: acreage 60%, zoning 40%
    total = 0.60 * acreage_s + 0.40 * zoning_s
    return total, notes


def _generation_subscore(gen: dict) -> tuple[float, list[str]]:
    notes: list[str] = []

    near = gen.get("nearestBaseload")
    if near is None:
        base_s = 30.0
        notes.append("No baseload (nuclear or large gas) plant found.")
    else:
        d = near.get("distanceMi", 999)
        if d <= 25:
            base_s = 100.0
        elif d <= 100:
            base_s = 70.0
        else:
            base_s = 40.0

    # Bonus: meaningful regional capacity
    cap = gen.get("capacityWithin25MiByFuel") or {}
    total_mw = sum(v for v in cap.values() if isinstance(v, (int, float)))
    bonus = 10.0 if total_mw >= 5000 else (5.0 if total_mw >= 2000 else 0.0)
    return min(100.0, base_s + bonus), notes


def _iso_subscore(iso: dict) -> tuple[float, list[str]]:
    name = (iso or {}).get("name") or "NON-ISO"
    if name == "NON-ISO":
        return 70.0, ["Vertically integrated utility region; bilateral process."]
    return 100.0, []


# --- F-gating ---------------------------------------------------------

def _gating_issues(grid: dict, infra: dict, gen: dict) -> list[str]:
    issues: list[str] = []

    # No transmission-class substation anywhere even loosely nearby.
    near_tx_sub = grid.get("nearestTransmissionSubstation")
    if near_tx_sub is None:
        issues.append("No >=115kV substation in dataset.")
    elif (near_tx_sub.get("distanceMi") or 999) > 25.0:
        issues.append("Nearest >=115kV substation is more than 25 mi away.")

    acres = infra.get("acreage")
    if acres is not None and acres < 1.0:
        issues.append("Parcel is too small (<1 acre) for any DC tier.")

    flood = (infra.get("floodZone") or "").upper()
    if flood in ("V", "VE", "FLOODWAY"):
        issues.append(f"Parcel in FEMA flood zone {flood} - no-build for DC.")

    wet = infra.get("wetlandCoveragePct")
    if wet is not None and wet > 75.0:
        issues.append("Parcel is >75% wetlands - buildable area negligible.")

    return issues


# --- top-level --------------------------------------------------------

@dataclass
class ScoringResult:
    composite: float
    letter: str
    rationale: str
    gating_issues: list[str]
    subscores: dict = field(default_factory=dict)


def score_report(report: dict) -> ScoringResult:
    """Take an analyzer subreport (the dict shape the analyzer is
    assembling) and return the scoring outcome.

    Required keys in `report`:
      grid (dict), generation (dict), power (dict), infrastructure (dict),
      iso (dict), zoning (str)
    """
    grid = report.get("grid") or {}
    gen = report.get("generation") or {}
    power = report.get("power") or {}
    infra = report.get("infrastructure") or {}
    iso = (grid.get("iso") or {}) if grid else {}
    zoning = report.get("zoning") or "unknown"

    grid_s, grid_notes = _grid_subscore(grid)
    cost_s, cost_notes = _power_cost_subscore(power)
    infra_s, infra_notes = _infra_subscore(infra)
    land_s, land_notes = _land_subscore(infra, zoning)
    gen_s, gen_notes = _generation_subscore(gen)
    iso_s, iso_notes = _iso_subscore(iso)

    composite = (
        grid_s * WEIGHTS["grid"]
        + cost_s * WEIGHTS["power_cost"]
        + infra_s * WEIGHTS["infrastructure"]
        + land_s * WEIGHTS["land"]
        + gen_s * WEIGHTS["generation"]
        + iso_s * WEIGHTS["iso"]
    )

    gating = _gating_issues(grid, infra, gen)
    if gating:
        letter = "F"
    else:
        letter = composite_to_letter(composite)

    notes = grid_notes + cost_notes + infra_notes + land_notes + gen_notes + iso_notes
    if gating:
        rationale = "F-gated: " + "; ".join(gating)
    else:
        rationale = (
            f"Composite {composite:.1f}/100 -> {letter}. "
            + ("Concerns: " + "; ".join(notes) if notes else "No major concerns.")
        )

    return ScoringResult(
        composite=round(composite, 1),
        letter=letter,
        rationale=rationale,
        gating_issues=gating,
        subscores={
            "grid": round(grid_s, 1),
            "power_cost": round(cost_s, 1),
            "infrastructure": round(infra_s, 1),
            "land": round(land_s, 1),
            "generation": round(gen_s, 1),
            "iso": round(iso_s, 1),
        },
    )


# --- substation grade convenience -------------------------------------

def substation_grade(distance_mi: Optional[float], max_voltage_kv: Optional[int]) -> str:
    """A/B/C/D rubric for substation proximity alone (per spec).

    Used for documentation / drill-down UI; the overall composite uses
    the continuous subscore in `_grid_subscore`.
    """
    if distance_mi is None or max_voltage_kv is None:
        return "D"
    if max_voltage_kv < 115:
        return "D"
    if distance_mi < 1.0:
        return "A"
    if distance_mi <= 3.0:
        return "B"
    if distance_mi <= 5.0:
        return "C"
    return "D"
