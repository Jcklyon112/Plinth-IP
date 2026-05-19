from app.engine.rules.base import RuleResult, RESULT_PASS, RESULT_CONDITIONAL, RESULT_FAIL, RESULT_UNKNOWN


def sewer_available_rule(parcel: dict, config: dict) -> RuleResult:
    """Check whether the municipality has sewer service."""
    has_sewer = config.get("sewer_service")
    confidence = config.get("sewer_service_confidence", 0.95 if has_sewer else 0.8)
    notes = config.get("sewer_service_notes", "")

    if has_sewer is None:
        return RuleResult(
            rule_id="sewer_available",
            rule_category="septic",
            result=RESULT_UNKNOWN,
            explanation=f"Sewer service status unconfirmed for this municipality. {notes}".strip(),
            assumptions_used={"sewer_service": None},
            confidence=0.0,
        )

    if has_sewer:
        return RuleResult(
            rule_id="sewer_available",
            rule_category="septic",
            result=RESULT_PASS,
            explanation=f"Municipality has sewer service. Septic capacity not a constraint. {notes}".strip(),
            assumptions_used={"sewer_service": True},
            confidence=confidence,
        )
    else:
        return RuleResult(
            rule_id="sewer_available",
            rule_category="septic",
            result=RESULT_CONDITIONAL,
            explanation=f"Municipality is not served by sewer. Septic feasibility must be evaluated. {notes}".strip(),
            assumptions_used={"sewer_service": False},
            confidence=confidence,
        )


def septic_capacity_rule(parcel: dict, config: dict, templates: list[dict]) -> RuleResult:
    """
    Evaluate whether the parcel can support a new or expanded septic system.

    Order of evidence:
      1. Public sewer available → PASS (rule not applicable).
      2. SSURGO soil-suitability rating from `parcel["soil_septic_class"]`,
         set by the SoilService when LiDAR/GIS coverage is present.
            "Not limited"      → PASS         (high confidence)
            "Somewhat limited" → CONDITIONAL  (engineering needed)
            "Very limited"     → FAIL         (conventional septic infeasible)
            "Not rated"/None   → fall through to lot-size heuristic
      3. Lot-size heuristic against config.septic_assumptions (legacy
         Phase 1 — used when no sewer flag and no soil data).
    """
    has_sewer = config.get("sewer_service")
    if has_sewer:
        return RuleResult(
            rule_id="septic_capacity",
            rule_category="septic",
            result=RESULT_PASS,
            explanation="Sewer available. Septic capacity rule not applicable.",
            assumptions_used={"sewer_service": True},
            confidence=1.0,
        )
    if has_sewer is None:
        return RuleResult(
            rule_id="septic_capacity",
            rule_category="septic",
            result=RESULT_UNKNOWN,
            explanation="Sewer service status unconfirmed. Cannot evaluate septic capacity without knowing sewer availability.",
            assumptions_used={"sewer_service": None},
            confidence=0.0,
        )

    # ── SSURGO soil suitability — preferred signal when available ────────
    soil_class = (parcel.get("soil_septic_class") or "").strip()
    soil_detail = parcel.get("soil_septic_detail") or {}
    if soil_class in ("Very limited", "Somewhat limited", "Not limited"):
        worst = soil_detail.get("worst") or {}
        component = worst.get("dominant_component") or "?"
        muname = worst.get("muname") or "?"
        common_assumptions = {
            "method": "ssurgo_soil_interpretation",
            "soil_septic_class": soil_class,
            "dominant_component": component,
            "muname": muname,
            "source": "USDA SSURGO",
        }
        if soil_class == "Not limited":
            return RuleResult(
                rule_id="septic_capacity",
                rule_category="septic",
                result=RESULT_PASS,
                explanation=(
                    f"SSURGO septic suitability: Not limited (soil: {component}, {muname}). "
                    "Soils favorable for conventional septic; perc test still recommended."
                ),
                assumptions_used=common_assumptions,
                confidence=0.85,
            )
        if soil_class == "Somewhat limited":
            return RuleResult(
                rule_id="septic_capacity",
                rule_category="septic",
                result=RESULT_CONDITIONAL,
                explanation=(
                    f"SSURGO septic suitability: Somewhat limited (soil: {component}, {muname}). "
                    "Conventional septic likely needs engineered design (e.g. mounded system, "
                    "shallow trenches). Perc test required."
                ),
                assumptions_used=common_assumptions,
                confidence=0.85,
            )
        # Very limited — SSURGO is coarse desk data (soil-survey polygons, not
        # parcel-level perc tests). A "Very limited" rating means conventional
        # septic is unlikely without an engineered I/A system, but that's a
        # cost/permit constraint, not a regulatory dead-end. Treat as a
        # CONDITIONAL with reduced confidence — the analyst should order a
        # perc test before ruling the parcel out. Returning FAIL here cratered
        # the septic_confidence category to 0 on parcels that were otherwise
        # solid leads.
        return RuleResult(
            rule_id="septic_capacity",
            rule_category="septic",
            result=RESULT_CONDITIONAL,
            explanation=(
                f"SSURGO septic suitability: Very limited (soil: {component}, {muname}). "
                "Conventional septic likely infeasible — engineered I/A system or variance "
                "may be required. Confirm with a perc test; SSURGO is coarse-survey data, "
                "not a substitute for site testing."
            ),
            assumptions_used=common_assumptions,
            confidence=0.6,
        )

    septic_config = config.get("septic_assumptions", {})
    min_lot_for_system = septic_config.get("min_lot_area_for_new_system_sqft")
    confidence = 0.6  # Septic is inherently uncertain without perc test data
    notes = septic_config.get("notes", "")
    lot_area = parcel.get("lot_area_sqft")

    if lot_area is None:
        return RuleResult(
            rule_id="septic_capacity",
            rule_category="septic",
            result=RESULT_UNKNOWN,
            explanation="Lot area not available. Cannot evaluate septic feasibility.",
            assumptions_used={"septic_config": septic_config},
            confidence=0.0,
        )

    if min_lot_for_system is None:
        return RuleResult(
            rule_id="septic_capacity",
            rule_category="septic",
            result=RESULT_UNKNOWN,
            explanation="Septic assumptions not configured. Cannot evaluate septic capacity.",
            assumptions_used={},
            confidence=0.0,
        )

    if lot_area >= min_lot_for_system:
        return RuleResult(
            rule_id="septic_capacity",
            rule_category="septic",
            result=RESULT_CONDITIONAL,
            explanation=(
                f"Lot area {lot_area:,.0f} sqft meets minimum {min_lot_for_system:,.0f} sqft for a new system. "
                f"Feasibility depends on soil conditions and existing system load. Perc test required. {notes}"
            ),
            assumptions_used={
                "min_lot_area_for_new_system_sqft": min_lot_for_system,
                "method": "lot_size_heuristic_phase1",
            },
            confidence=confidence,
        )
    else:
        return RuleResult(
            rule_id="septic_capacity",
            rule_category="septic",
            result=RESULT_FAIL,
            explanation=(
                f"Lot area {lot_area:,.0f} sqft is below minimum {min_lot_for_system:,.0f} sqft required "
                f"for a new septic system. Additional bedrooms unlikely to be supported."
            ),
            assumptions_used={
                "min_lot_area_for_new_system_sqft": min_lot_for_system,
                "method": "lot_size_heuristic_phase1",
            },
            confidence=confidence,
        )
