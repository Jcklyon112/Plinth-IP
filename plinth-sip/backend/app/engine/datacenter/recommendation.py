"""Anchor-component recommendation + Rhino-bound parcel readout.

Two pure-Python functions:

* `recommend_anchor_component(report)` picks ONE piece of DC equipment that
  should anchor the layout, given an analyzer report. The choice is driven
  by the parcel's acreage tier, the nearest transmission-class substation
  voltage and distance, and any gating issues. Returns a dict with the
  component name, implied IT capacity, count, footprint, and a one-line
  rationale.

* `format_parcel_readout(report)` returns the multi-line text block that
  the GH READOUT layer bakes above the parcel / building. Combines the
  parcel-info, ISO/grid-lookup, and recommendation into one human-readable
  card. Lines are joined with `\\n` so the existing IronPython side of
  the template (which expects literal `\\n` runtime newlines) can embed
  the string after a single `.replace("\\n", "\\\\n")` pass during
  substitution.

Neither function touches the DB or imports SQLAlchemy/PostGIS - they
operate on the report dict produced by `analyzer._build_report()`. That
keeps them unit-testable without a Postgres fixture.
"""
from __future__ import annotations

from typing import Optional


# --- anchor-component catalog ----------------------------------------
#
# Hand-picked menu of canonical anchors. Each entry's `kw_envelope` is the
# rough IT load window the anchor is sized for; the recommender picks the
# row whose envelope contains the parcel's implied IT load. Footprints
# are gross "drop-on-pad" plan dimensions for the equipment lineup itself
# (excluding service clearance) - they're shown in the readout so the
# designer can sanity-check whether the parcel can host the lineup at all.
#
# Capacity (`it_capacity_mw`) is the IT-load ceiling the anchor supports
# at the assumed PUE=1.4. Equipment counts pair with the GH template's
# proportional model (15 kW/rack, 500 kW/UPS, 1500 kW/gen, etc.).

_ANCHORS = {
    "edge_2mva": {
        "name": "2 MVA pad-mount transformer + 60-rack single pod",
        "it_capacity_mw": 1.5,
        "racks": 60,
        "footprint_ft": "8 x 8 x 6 (transformer) on a 65 x 100 ft pad-mount slab",
        "tier": "edge",
    },
    "colo_dual_5mva": {
        "name": "2x 5 MVA pad-mount transformers (N+1)",
        "it_capacity_mw": 6.0,
        "racks": 240,
        "footprint_ft": "two 12 x 14 ft pads, 20 ft separation",
        "tier": "colo",
    },
    "colo_10mva_primary": {
        "name": "10 MVA primary substation interconnect (138/13.8 kV step-down)",
        "it_capacity_mw": 8.5,
        "racks": 340,
        "footprint_ft": "30 x 60 ft fenced lineup",
        "tier": "colo",
    },
    "hyperscale_20mva_dual": {
        "name": "20 MVA dual primary feed (138 kV, redundant feeders)",
        "it_capacity_mw": 16.0,
        "racks": 640,
        "footprint_ft": "60 x 80 ft fenced lineup",
        "tier": "hyperscale",
    },
    "hyperscale_35mva_primary": {
        "name": "35 MVA primary substation lineup (138/345 kV step-down)",
        "it_capacity_mw": 28.0,
        "racks": 1100,
        "footprint_ft": "100 x 120 ft fenced lineup",
        "tier": "hyperscale",
    },
    "campus_switchyard": {
        "name": "Dedicated 138/345 kV switchyard, 4x 35 MVA transformers",
        "it_capacity_mw": 100.0,
        "racks": 4000,
        "footprint_ft": "300 x 400 ft switchyard",
        "tier": "campus",
    },
    "onsite_gas_chp": {
        "name": "On-site gas-fired generation (50 MW CHP plant) - grid-bypass anchor",
        "it_capacity_mw": 35.0,
        "racks": 1400,
        "footprint_ft": "200 x 300 ft plant + 100 x 100 ft fuel/water yard",
        "tier": "hyperscale",
    },
}


# Voltage cutoff above which we treat a substation feeder as
# "transmission-class" enough to anchor a hyperscale lineup directly.
_HYPERSCALE_KV = 230
_PRIMARY_KV = 138

# Distance cutoffs (mi). Anything past `_FAR_MI` from a transmission
# substation pushes us toward on-site generation, regardless of acreage.
_NEAR_MI = 2.0
_FAR_MI = 10.0


def recommend_anchor_component(report: dict) -> dict:
    """Pick the single anchoring DC building block for this parcel.

    Returns a dict shaped like:
        {
            "name": str,                    # human-readable component label
            "itCapacityMw": float,          # implied IT load ceiling
            "rackCount": int,               # rough rack count it powers
            "footprintFt": str,             # plan-dimension hint
            "tier": str,                    # edge | colo | hyperscale | campus
            "rationale": str,               # one line, names the drivers
            "gating": Optional[str],        # populated when no anchor fits
        }
    """
    grid = report.get("grid") or {}
    infra = report.get("infrastructure") or {}
    gating = report.get("gatingIssues") or []
    iso = (grid.get("iso") or {}).get("name") or "NON-ISO"

    tier = infra.get("acreageTier")
    acreage = infra.get("acreage")

    nearest_tx = grid.get("nearestTransmissionSubstation") or {}
    sub_kv = nearest_tx.get("maxVoltageKv")
    sub_mi = nearest_tx.get("distanceMi")
    has_230_within_1 = bool(grid.get("has230kvLineWithin1Mi"))
    dual_feed = bool(grid.get("dualFeedFeasible"))

    nearest_gas_mi = infra.get("gasPipelineDistanceMi")

    # 1) Hard gates first - if the parcel is being scored F for a
    #    fundamental reason, surface that and skip the menu entirely.
    if gating:
        return {
            "name": "(no anchor - gating issue)",
            "itCapacityMw": 0.0,
            "rackCount": 0,
            "footprintFt": "-",
            "tier": tier or "unknown",
            "rationale": "; ".join(gating[:2]),
            "gating": gating[0],
        }

    # 2) Substation-far rescue: if the nearest transmission-class
    #    substation is past _FAR_MI and a gas pipeline is close, the
    #    economics flip toward on-site generation - call that out
    #    rather than hand-waving a transformer the grid can't deliver to.
    far_from_grid = sub_mi is None or sub_mi > _FAR_MI
    gas_close = nearest_gas_mi is not None and nearest_gas_mi <= 1.0
    if far_from_grid and gas_close and tier in ("colo", "hyperscale", "campus"):
        a = _ANCHORS["onsite_gas_chp"]
        return {
            **{k: v for k, v in a.items() if k != "tier"},
            "tier": tier,
            "rationale": _format_rationale(
                tier,
                sub_kv,
                sub_mi,
                iso,
                acreage,
                extra="gas pipeline {0:.1f} mi -> on-site generation cheaper than long primary tap".format(nearest_gas_mi),
            ),
            "gating": None,
        }

    # 3) Tier-driven menu pick.
    if tier == "edge" or (tier is None and (acreage or 0) < 5):
        key = "edge_2mva"
    elif tier == "colo":
        # Primary substation lineup wins when the parcel is close to a
        # transmission-class feeder; otherwise stick to dual pad-mounts.
        if sub_kv and sub_kv >= _PRIMARY_KV and sub_mi is not None and sub_mi <= _NEAR_MI:
            key = "colo_10mva_primary"
        else:
            key = "colo_dual_5mva"
    elif tier == "hyperscale":
        # 35 MVA only makes sense if there's actually a 230 kV+ feeder
        # within reach; otherwise fall back to a 20 MVA dual feed (still
        # plausible at 138 kV) or, when the grid is too far, on-site gas.
        if sub_kv and sub_kv >= _HYPERSCALE_KV and sub_mi is not None and sub_mi <= _NEAR_MI:
            key = "hyperscale_35mva_primary"
        elif sub_kv and sub_kv >= _PRIMARY_KV and sub_mi is not None and sub_mi <= _NEAR_MI:
            key = "hyperscale_20mva_dual"
        else:
            key = "onsite_gas_chp"
    elif tier == "campus":
        # >100 ac. If there's already 230 kV within a mile of the parcel
        # boundary, a dedicated switchyard makes sense; otherwise the
        # hyperscale anchor degrades gracefully.
        key = "campus_switchyard" if has_230_within_1 else "hyperscale_35mva_primary"
    else:
        # Unknown acreage tier - bail out rather than guess.
        return {
            "name": "(insufficient data - acreage tier unknown)",
            "itCapacityMw": 0.0,
            "rackCount": 0,
            "footprintFt": "-",
            "tier": tier or "unknown",
            "rationale": "Acreage missing from parcel record - load lot_area_sqft to score.",
            "gating": "missing_acreage",
        }

    a = _ANCHORS[key]
    return {
        **{k: v for k, v in a.items() if k != "tier"},
        "tier": tier,
        "rationale": _format_rationale(tier, sub_kv, sub_mi, iso, acreage,
                                       dual_feed=dual_feed,
                                       has_230_within_1=has_230_within_1),
        "gating": None,
    }


def _format_rationale(
    tier: Optional[str],
    sub_kv: Optional[int],
    sub_mi: Optional[float],
    iso: str,
    acreage: Optional[float],
    *,
    dual_feed: bool = False,
    has_230_within_1: bool = False,
    extra: Optional[str] = None,
) -> str:
    parts = []
    if acreage is not None and tier:
        parts.append("{0} tier ({1:.1f} ac)".format(tier, acreage))
    elif tier:
        parts.append("{0} tier".format(tier))
    if sub_kv and sub_mi is not None:
        parts.append("nearest {0} kV substation {1:.1f} mi".format(sub_kv, sub_mi))
    elif sub_kv is None:
        parts.append("no transmission-class substation in dataset")
    if has_230_within_1:
        parts.append("230 kV line within 1 mi")
    if dual_feed:
        parts.append("dual-feed feasible")
    if iso and iso != "NON-ISO":
        parts.append("{0} interconnect".format(iso))
    if extra:
        parts.append(extra)
    return "; ".join(parts) if parts else "Heuristic default."


# --- formatted Rhino readout -----------------------------------------

_RULE = "=" * 50

def format_parcel_readout(report: dict, *, include_recommendation: bool = True) -> str:
    """Build the multi-line readout block to bake on the Rhino READOUT layer.

    Output is suitable for the GH template's existing readout convention:
    one string with `\\n` separators, ready to be `.replace("\\n", "\\\\n")`'d
    into the IronPython template's substitution slot.
    """
    grid = report.get("grid") or {}
    infra = report.get("infrastructure") or {}
    power = report.get("power") or {}
    gen = report.get("generation") or {}
    iso = (grid.get("iso") or {})
    iso_name = iso.get("name") or "UNKNOWN"

    nearest_sub = grid.get("nearestSubstation") or {}
    nearest_tx = grid.get("nearestTransmissionSubstation") or {}
    nearest_line = grid.get("nearestTransmissionLine") or {}
    fiber = grid_get_fiber(infra)
    gas = infra.get("nearestGasPipeline") or {}

    rl: list[str] = []
    rl.append("PLINTH PARCEL FEASIBILITY  -  ANALYZER LOOKUP")
    rl.append(_RULE)

    # 1) Identity / footprint
    addr = report.get("address") or "(address unset)"
    pid = report.get("parcelId") or "(no parcel_id)"
    centroid = report.get("parcelCentroid") or [None, None]
    rl.append("Address: {0}".format(addr))
    rl.append("Parcel:  {0}".format(pid))
    if centroid[0] is not None:
        rl.append("Centroid: {0:.5f}, {1:.5f}".format(centroid[1], centroid[0]))
    if infra.get("acreage") is not None:
        rl.append("Acreage: {0:.2f} ac   (tier: {1})".format(
            infra["acreage"], infra.get("acreageTier") or "?"))
    rl.append("Zoning:  {0}".format(report.get("zoning") or "unknown"))

    rl.append(_RULE)

    # 2) ISO / grid lookup
    rl.append("ISO / RTO: {0}".format(iso_name))
    if iso.get("fullName"):
        rl.append("           {0}".format(iso["fullName"]))
    if iso.get("typicalQueueTimeline"):
        rl.append("  Queue:    {0}".format(iso["typicalQueueTimeline"]))

    if nearest_tx:
        rl.append("Nearest >=115 kV substation:")
        rl.append("  {0} ({1} kV) - {2:.2f} mi".format(
            nearest_tx.get("name") or "(unnamed)",
            nearest_tx.get("maxVoltageKv") or "?",
            nearest_tx.get("distanceMi") or 0.0,
        ))
    elif nearest_sub:
        rl.append("Nearest substation (sub-transmission only):")
        rl.append("  {0} ({1} kV) - {2:.2f} mi".format(
            nearest_sub.get("name") or "(unnamed)",
            nearest_sub.get("maxVoltageKv") or "?",
            nearest_sub.get("distanceMi") or 0.0,
        ))
    else:
        rl.append("Nearest substation: (no grid data loaded)")

    if nearest_line:
        rl.append("Nearest transmission line: {0} kV @ {1:.2f} mi".format(
            nearest_line.get("voltageKv") or "?",
            nearest_line.get("distanceMi") or 0.0,
        ))
    if grid.get("has230kvLineWithin1Mi"):
        rl.append("  >= 230 kV line within 1 mi.")
    if grid.get("dualFeedFeasible"):
        rl.append("  Dual-feed feasible (>=2 corridors within 5 mi).")

    rl.append(_RULE)

    # 3) Power cost / utility
    util = power.get("utility")
    rate = power.get("industrialRateCentsPerKwh")
    tier = power.get("rateTier")
    if util:
        rl.append("Utility:    {0}".format(util))
    if rate is not None:
        rl.append("Industrial rate: {0:.2f} cents/kWh ({1})".format(rate, tier or "?"))
    elif util:
        rl.append("Industrial rate: (no EIA Form 861 data loaded)")

    # 4) Other infrastructure
    fiber_mi = infra.get("fiberDistanceMi")
    gas_mi = infra.get("gasPipelineDistanceMi")
    if fiber_mi is not None:
        rl.append("Fiber:      {0:.2f} mi".format(fiber_mi))
    if gas_mi is not None:
        rl.append("Gas pipe:   {0:.2f} mi  ({1})".format(gas_mi, gas.get("operator") or "?"))
    if infra.get("floodZone"):
        rl.append("Flood zone: {0}".format(infra["floodZone"]))
    if infra.get("wetlandCoveragePct") is not None:
        rl.append("Wetlands:   {0:.1f}%".format(infra["wetlandCoveragePct"]))

    # 5) Generation context
    nb = gen.get("nearestBaseload") or {}
    if nb:
        rl.append("Nearest baseload plant: {0} ({1}, {2:.0f} MW) - {3:.1f} mi".format(
            nb.get("name") or "(unnamed)",
            nb.get("fuel") or "?",
            nb.get("capacityMw") or 0.0,
            nb.get("distanceMi") or 0.0,
        ))

    rl.append(_RULE)

    # 6) Score
    rl.append("Overall score: {0}  (composite {1:.1f})".format(
        report.get("overallScore") or "?",
        report.get("compositeScore") or 0.0,
    ))
    if report.get("scoreRationale"):
        rl.append("  {0}".format(report["scoreRationale"]))
    if report.get("gatingIssues"):
        rl.append("Gating issues:")
        for g in report["gatingIssues"]:
            rl.append("  ! {0}".format(g))

    # 7) Recommendation (computed inline if not present)
    if include_recommendation:
        rec = report.get("recommendation") or recommend_anchor_component(report)
        rl.append(_RULE)
        rl.append("RECOMMENDED ANCHOR COMPONENT")
        rl.append("  {0}".format(rec.get("name") or "(none)"))
        cap = rec.get("itCapacityMw")
        racks = rec.get("rackCount")
        if cap and racks:
            rl.append("  Sized for ~{0:.1f} MW IT  (~{1} racks @ 15 kW)".format(cap, racks))
        if rec.get("footprintFt") and rec["footprintFt"] != "-":
            rl.append("  Footprint: {0}".format(rec["footprintFt"]))
        if rec.get("rationale"):
            rl.append("  Why: {0}".format(rec["rationale"]))
        if rec.get("gating"):
            rl.append("  ** GATING: {0}".format(rec["gating"]))

    # Warnings tail
    warns = report.get("warnings") or []
    if warns:
        rl.append(_RULE)
        rl.append("WARNINGS:")
        for w in warns:
            rl.append("  ! {0}".format(w))

    return "\n".join(rl)


def grid_get_fiber(infra: dict) -> dict:
    """Helper for the fiber-distance line; tolerates either the nearestFiber
    nested block or the bare distance scalar from older report shapes."""
    nf = infra.get("nearestFiber")
    return nf if isinstance(nf, dict) else {}
