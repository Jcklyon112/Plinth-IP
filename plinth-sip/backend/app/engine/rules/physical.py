from app.engine.rules.base import RuleResult, RESULT_PASS, RESULT_CONDITIONAL, RESULT_FAIL, RESULT_UNKNOWN

HARD_BLOCK_OVERLAYS = {"flood_zone_ae", "flood_zone_a", "wetlands_buffer", "conservation_restriction"}
REVIEW_REQUIRED_OVERLAYS = {"historic_district", "flood_zone_x", "scenic_road_corridor"}


def overlay_constraints_rule(parcel: dict, config: dict) -> RuleResult:
    """
    Check parcel for environmental/regulatory overlays.

    Primary source: `overlay_hits` populated by the live OverlayService (FEMA
    flood, MA wetlands w/ 100-ft buffer, NHESP habitat, ACEC, 21E sites,
    protected open space, historic inventory, wellhead protection, etc.).
    Each hit carries a constraint_level: hard_block | review | soft_constraint.

    Fallback: if no overlay_hits present, use the legacy `constraints_flags`
    string list matched against config overlays (Phase 1 behavior).
    """
    overlay_hits = parcel.get("overlay_hits") or []

    # Normalize legacy "review_required" → "review" so both paths agree
    def _level(h):
        lvl = (h.get("constraint_level") or "").lower()
        return "review" if lvl == "review_required" else lvl

    if overlay_hits:
        hard_blocks = [h for h in overlay_hits if _level(h) == "hard_block"]
        reviews = [h for h in overlay_hits if _level(h) == "review"]

        if hard_blocks:
            labels = [h.get("label", h.get("layer_id", "?")) for h in hard_blocks]
            return RuleResult(
                rule_id="overlay_constraints",
                rule_category="physical",
                result=RESULT_FAIL,
                explanation=f"Parcel intersects hard-block overlay(s): {', '.join(labels)}. Deployment not feasible without variance/permit.",
                assumptions_used={
                    "source": "live_spatial_intersection",
                    "hard_block_overlays": labels,
                    "overlay_hits": hard_blocks,
                },
                confidence=0.9,
            )

        if reviews:
            labels = [h.get("label", h.get("layer_id", "?")) for h in reviews]
            return RuleResult(
                rule_id="overlay_constraints",
                rule_category="physical",
                result=RESULT_CONDITIONAL,
                explanation=f"Parcel is in review-required overlay(s): {', '.join(labels)}. Deployment may be possible with approval.",
                assumptions_used={
                    "source": "live_spatial_intersection",
                    "review_overlays": labels,
                    "overlay_hits": reviews,
                },
                confidence=0.85,
            )

        # All hits are soft_constraint or unknown — note them but pass
        soft_labels = [h.get("label", h.get("layer_id", "?")) for h in overlay_hits]
        return RuleResult(
            rule_id="overlay_constraints",
            rule_category="physical",
            result=RESULT_PASS,
            explanation=(
                f"No blocking overlays. Soft constraints noted: {', '.join(soft_labels)}."
                if soft_labels
                else "No blocking overlays detected."
            ),
            assumptions_used={"source": "live_spatial_intersection", "soft_overlays": soft_labels},
            confidence=0.85,
        )

    # ─── Legacy fallback: config overlays + constraints_flags strings ────
    constraint_flags = parcel.get("constraints_flags", [])
    config_overlays = config.get("overlays", [])

    if not config_overlays and not constraint_flags:
        return RuleResult(
            rule_id="overlay_constraints",
            rule_category="physical",
            result=RESULT_PASS,
            explanation="No overlays evaluated (live overlay service unavailable; no config overlays defined).",
            assumptions_used={"source": "fallback_no_data"},
            confidence=0.5,
        )

    hard_blocks: list[str] = []
    review_flags: list[str] = []
    for flag in constraint_flags:
        flag_lower = str(flag).lower().replace(" ", "_")
        for overlay in config_overlays:
            overlay_type = overlay.get("overlay_type", "").lower().replace(" ", "_")
            constraint_level = overlay.get("constraint_level", "")
            if overlay_type and (overlay_type in flag_lower or flag_lower in overlay_type):
                if constraint_level == "hard_block":
                    hard_blocks.append(overlay.get("label", overlay_type))
                elif constraint_level in ("review_required", "review"):
                    review_flags.append(overlay.get("label", overlay_type))

    if hard_blocks:
        return RuleResult(
            rule_id="overlay_constraints",
            rule_category="physical",
            result=RESULT_FAIL,
            explanation=f"Parcel intersects hard-block overlay(s): {hard_blocks}. Deployment not feasible without variance.",
            assumptions_used={"source": "config_flags", "hard_block_overlays": hard_blocks},
            confidence=0.7,
        )

    if review_flags:
        return RuleResult(
            rule_id="overlay_constraints",
            rule_category="physical",
            result=RESULT_CONDITIONAL,
            explanation=f"Parcel is in a review-required overlay: {review_flags}.",
            assumptions_used={"source": "config_flags", "review_overlays": review_flags},
            confidence=0.65,
        )

    return RuleResult(
        rule_id="overlay_constraints",
        rule_category="physical",
        result=RESULT_PASS,
        explanation="No blocking overlays detected based on parcel constraint flags.",
        assumptions_used={"source": "config_flags", "constraint_flags_checked": constraint_flags},
        confidence=0.65,
    )


def slope_buildability_rule(parcel: dict, district_config: dict) -> RuleResult:
    """
    Score the parcel by terrain slope (degrees) computed from LiDAR DEM.

    Reads `parcel["slope_stats"]` populated by the ElevationService (mean
    and max slope in degrees). Bands:
      mean ≤ 8°   (≈14% rise) → PASS         — flat to gentle, normal cost
      mean ≤ 15°  (≈27% rise) → CONDITIONAL  — significant grading + cost
      mean > 15°                → FAIL        — typically infeasible w/o major civil work
    A very high `max` slope (>25°) downgrades a PASS to CONDITIONAL because
    a single steep band can still block siting even when the lot averages flat.
    """
    slope = parcel.get("slope_stats")
    if not slope or slope.get("count", 0) == 0:
        return RuleResult(
            rule_id="slope_buildability",
            rule_category="physical",
            result=RESULT_UNKNOWN,
            explanation="Slope unavailable — no LiDAR DEM coverage or geometry missing.",
            assumptions_used={"source": "missing"},
            confidence=0.0,
        )

    mean_deg = float(slope.get("mean", 0.0))
    max_deg = float(slope.get("max", 0.0))
    pct_rise_mean = _deg_to_pct(mean_deg)
    pct_rise_max = _deg_to_pct(max_deg)
    src = slope.get("source", "DEM")

    base = {
        "source": src,
        "mean_deg": round(mean_deg, 1),
        "max_deg": round(max_deg, 1),
        "mean_pct_rise": round(pct_rise_mean, 1),
        "max_pct_rise": round(pct_rise_max, 1),
    }

    if mean_deg > 15.0:
        return RuleResult(
            rule_id="slope_buildability",
            rule_category="physical",
            result=RESULT_FAIL,
            explanation=(
                f"Mean slope {mean_deg:.0f}° (~{pct_rise_mean:.0f}% rise) — too steep for "
                "standard ADU placement. Major retaining walls / foundation engineering needed."
            ),
            assumptions_used=base,
            confidence=0.85,
        )

    if mean_deg > 8.0:
        return RuleResult(
            rule_id="slope_buildability",
            rule_category="physical",
            result=RESULT_CONDITIONAL,
            explanation=(
                f"Mean slope {mean_deg:.0f}° (~{pct_rise_mean:.0f}% rise) — moderately sloped; "
                "expect added foundation/grading cost (~10–20% premium)."
            ),
            assumptions_used=base,
            confidence=0.85,
        )

    if max_deg > 25.0:
        return RuleResult(
            rule_id="slope_buildability",
            rule_category="physical",
            result=RESULT_CONDITIONAL,
            explanation=(
                f"Lot averages flat (mean {mean_deg:.0f}°) but contains a steep band "
                f"(max {max_deg:.0f}° / {pct_rise_max:.0f}% rise). Siting must avoid the steep zone."
            ),
            assumptions_used=base,
            confidence=0.8,
        )

    return RuleResult(
        rule_id="slope_buildability",
        rule_category="physical",
        result=RESULT_PASS,
        explanation=f"Mean slope {mean_deg:.0f}° (~{pct_rise_mean:.0f}% rise) — buildable terrain.",
        assumptions_used=base,
        confidence=0.9,
    )


def _deg_to_pct(deg: float) -> float:
    """Convert slope from degrees to percent rise."""
    import math
    return math.tan(math.radians(deg)) * 100.0


def electrical_service_rule(parcel: dict, district_config: dict) -> RuleResult:
    """
    Estimate likelihood that the existing service panel can carry an added
    ADU load without an expensive utility/transformer upgrade.

    Phase 1 heuristic — uses `year_built` as a proxy for typical service
    amperage in residential construction:
      built ≥ 1986        → likely 200A → PASS    (no panel upgrade expected)
      built 1960–1985     → likely 100A → CONDITIONAL (~$1.5–3.5k panel
                                                        upgrade likely)
      built < 1960 or N/A → likely ≤100A, possibly knob-and-tube wiring →
                            CONDITIONAL with higher upgrade-cost note.

    Never returns FAIL — electrical capacity is a cost driver, not a
    hard regulatory blocker. A surprised analyst is better than a parcel
    incorrectly killed for old-house bias.
    """
    year_built = parcel.get("year_built")
    try:
        yb = int(year_built) if year_built is not None else None
    except (TypeError, ValueError):
        yb = None

    if yb is None or yb <= 0:
        return RuleResult(
            rule_id="electrical_service",
            rule_category="physical",
            result=RESULT_UNKNOWN,
            explanation=(
                "Year built unavailable — cannot estimate panel capacity. "
                "Plan to verify panel amperage on site visit."
            ),
            assumptions_used={"method": "year_built_proxy", "year_built": None},
            confidence=0.0,
        )

    if yb >= 1986:
        return RuleResult(
            rule_id="electrical_service",
            rule_category="physical",
            result=RESULT_PASS,
            explanation=(
                f"Built {yb} — service panel likely already 200A; ADU load "
                "should be addable without utility-side upgrade."
            ),
            assumptions_used={"method": "year_built_proxy", "year_built": yb, "expected_amperage": 200},
            confidence=0.55,
        )

    if yb >= 1960:
        return RuleResult(
            rule_id="electrical_service",
            rule_category="physical",
            result=RESULT_CONDITIONAL,
            explanation=(
                f"Built {yb} — service likely 100A. ADU load typically requires "
                "panel upgrade to 200A (~$1.5k–$3.5k). Verify on site visit."
            ),
            assumptions_used={"method": "year_built_proxy", "year_built": yb, "expected_amperage": 100},
            confidence=0.55,
        )

    return RuleResult(
        rule_id="electrical_service",
        rule_category="physical",
        result=RESULT_CONDITIONAL,
        explanation=(
            f"Built {yb} — pre-1960 service likely ≤100A and may include "
            "knob-and-tube or aluminum wiring. Expect $5k–$25k for panel + "
            "service-drop upgrade; verify on site visit before commit."
        ),
        assumptions_used={"method": "year_built_proxy", "year_built": yb, "expected_amperage": 60},
        confidence=0.55,
    )


def access_likely_rule(parcel: dict, district_config: dict) -> RuleResult:
    """
    Estimate rear-yard access likelihood.
    Uses actual frontage data when available (NY parcels), falls back to lot area heuristic.
    """
    lot_area = parcel.get("lot_area_sqft")
    actual_frontage = parcel.get("frontage_ft")

    if lot_area is None and actual_frontage is None:
        return RuleResult(
            rule_id="access_likely",
            rule_category="physical",
            result=RESULT_UNKNOWN,
            explanation="Lot data not available. Cannot estimate access likelihood.",
            assumptions_used={"method": "heuristic_phase1"},
            confidence=0.0,
        )

    # Use actual frontage when available (much more accurate)
    if actual_frontage is not None and actual_frontage > 0:
        if actual_frontage >= 40:
            return RuleResult(
                rule_id="access_likely",
                rule_category="physical",
                result=RESULT_PASS,
                explanation=f"Frontage {actual_frontage:.0f}ft provides adequate side/rear access for delivery.",
                assumptions_used={"frontage_ft": actual_frontage, "method": "actual_frontage"},
                confidence=0.8,
            )
        elif actual_frontage >= 20:
            return RuleResult(
                rule_id="access_likely",
                rule_category="physical",
                result=RESULT_CONDITIONAL,
                explanation=f"Frontage {actual_frontage:.0f}ft is narrow. Side access may be tight — site inspection recommended.",
                assumptions_used={"frontage_ft": actual_frontage, "method": "actual_frontage"},
                confidence=0.7,
            )
        else:
            return RuleResult(
                rule_id="access_likely",
                rule_category="physical",
                result=RESULT_CONDITIONAL,
                explanation=f"Frontage {actual_frontage:.0f}ft is very narrow. Delivery access will be challenging.",
                assumptions_used={"frontage_ft": actual_frontage, "method": "actual_frontage"},
                confidence=0.7,
            )

    # Fallback: lot area heuristic
    if lot_area >= 43560:  # >= 1 acre
        return RuleResult(
            rule_id="access_likely",
            rule_category="physical",
            result=RESULT_PASS,
            explanation=f"Lot area {lot_area:,.0f} sqft suggests adequate space for rear-yard access.",
            assumptions_used={"method": "lot_area_heuristic", "threshold_sqft": 43560},
            confidence=0.6,
        )
    elif lot_area >= 15000:
        return RuleResult(
            rule_id="access_likely",
            rule_category="physical",
            result=RESULT_CONDITIONAL,
            explanation=f"Lot area {lot_area:,.0f} sqft may support access but site inspection recommended.",
            assumptions_used={"method": "lot_area_heuristic"},
            confidence=0.5,
        )
    else:
        return RuleResult(
            rule_id="access_likely",
            rule_category="physical",
            result=RESULT_CONDITIONAL,
            explanation=f"Small lot ({lot_area:,.0f} sqft). Access may be constrained. Manual review recommended.",
            assumptions_used={"method": "lot_area_heuristic"},
            confidence=0.4,
        )
