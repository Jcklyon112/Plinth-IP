from app.engine.rules.base import RuleResult, RESULT_PASS, RESULT_CONDITIONAL, RESULT_FAIL, RESULT_UNKNOWN


def delivery_access_rule(parcel: dict, district_config: dict) -> RuleResult:
    """
    Estimate delivery access. Uses actual parcel frontage when available,
    falls back to district min_frontage heuristic.
    Plinth units arrive by truck and require reasonable road access.
    """
    lot_area = parcel.get("lot_area_sqft")
    actual_frontage = parcel.get("frontage_ft")
    min_frontage = district_config.get("min_frontage_ft")

    # Use actual parcel frontage when available (much better than district default)
    if actual_frontage is not None and actual_frontage > 0:
        if actual_frontage >= 50:
            return RuleResult(
                rule_id="delivery_access",
                rule_category="deployment",
                result=RESULT_PASS,
                explanation=f"Parcel frontage {actual_frontage:.0f}ft provides adequate road access for delivery truck.",
                assumptions_used={"frontage_ft": actual_frontage, "method": "actual_frontage"},
                confidence=0.8,
            )
        elif actual_frontage >= 25:
            return RuleResult(
                rule_id="delivery_access",
                rule_category="deployment",
                result=RESULT_CONDITIONAL,
                explanation=f"Parcel frontage {actual_frontage:.0f}ft is moderate. Delivery feasible but may need special routing.",
                assumptions_used={"frontage_ft": actual_frontage, "method": "actual_frontage"},
                confidence=0.7,
            )
        else:
            return RuleResult(
                rule_id="delivery_access",
                rule_category="deployment",
                result=RESULT_CONDITIONAL,
                explanation=f"Parcel frontage {actual_frontage:.0f}ft is narrow. Crane or specialized delivery may be required.",
                assumptions_used={"frontage_ft": actual_frontage, "method": "actual_frontage"},
                confidence=0.7,
            )

    if lot_area is None:
        return RuleResult(
            rule_id="delivery_access",
            rule_category="deployment",
            result=RESULT_UNKNOWN,
            explanation="Lot data insufficient to evaluate delivery access. Manual review recommended.",
            assumptions_used={"method": "heuristic_phase1"},
            confidence=0.0,
        )

    # Fallback 1: use district min_frontage
    if min_frontage and min_frontage >= 100:
        return RuleResult(
            rule_id="delivery_access",
            rule_category="deployment",
            result=RESULT_PASS,
            explanation=f"District minimum frontage {min_frontage}ft suggests adequate road access for delivery.",
            assumptions_used={"min_frontage_ft": min_frontage, "method": "district_heuristic"},
            confidence=0.65,
        )
    elif min_frontage and min_frontage >= 60:
        # Fallback 2: combine min_frontage with lot area
        # Large lots almost certainly have adequate road frontage even if we don't measure it
        if lot_area and lot_area >= 43560:  # >= 1 acre
            return RuleResult(
                rule_id="delivery_access",
                rule_category="deployment",
                result=RESULT_PASS,
                explanation=(
                    f"Lot area {lot_area:,.0f} sqft with district minimum frontage {min_frontage}ft "
                    "suggests adequate road access for delivery."
                ),
                assumptions_used={
                    "lot_area_sqft": lot_area,
                    "min_frontage_ft": min_frontage,
                    "method": "lot_area_plus_district_heuristic",
                },
                confidence=0.6,
            )
        return RuleResult(
            rule_id="delivery_access",
            rule_category="deployment",
            result=RESULT_CONDITIONAL,
            explanation=f"District minimum frontage {min_frontage}ft. Delivery access may be limited — site visit recommended.",
            assumptions_used={"min_frontage_ft": min_frontage, "method": "district_heuristic"},
            confidence=0.55,
        )
    else:
        # Final fallback: large lot heuristic when no frontage data available
        if lot_area and lot_area >= 43560:
            return RuleResult(
                rule_id="delivery_access",
                rule_category="deployment",
                result=RESULT_CONDITIONAL,
                explanation=(
                    f"Lot area {lot_area:,.0f} sqft suggests space for access, but no frontage data available. "
                    "Manual review of road frontage recommended."
                ),
                assumptions_used={"lot_area_sqft": lot_area, "method": "lot_area_heuristic"},
                confidence=0.5,
            )
        return RuleResult(
            rule_id="delivery_access",
            rule_category="deployment",
            result=RESULT_CONDITIONAL,
            explanation="Frontage data insufficient. Delivery access uncertain. Manual review required.",
            assumptions_used={"method": "heuristic_phase1"},
            confidence=0.4,
        )


def existing_structures_rule(parcel: dict, district_config: dict) -> RuleResult:
    """
    Flag parcels with multiple existing structures as more complex siting scenarios.
    Falls back to building area when explicit count is not available.
    """
    structure_count = parcel.get("existing_structure_count")

    # Infer structure presence from building footprint area when count not available
    if structure_count is None:
        bld_area = parcel.get("existing_building_footprint_area")
        if bld_area is not None and bld_area > 0:
            structure_count = 1  # Assume at least one structure exists
        elif bld_area == 0:
            structure_count = 0  # Explicitly empty lot

    if structure_count is None:
        return RuleResult(
            rule_id="existing_structures",
            rule_category="deployment",
            result=RESULT_UNKNOWN,
            explanation="Existing structure count not available from parcel data.",
            assumptions_used={},
            confidence=0.0,
        )

    if structure_count == 0:
        return RuleResult(
            rule_id="existing_structures",
            rule_category="deployment",
            result=RESULT_PASS,
            explanation="No existing structures on parcel. Siting flexibility is high.",
            assumptions_used={"existing_structure_count": 0},
            confidence=0.8,
        )
    elif structure_count == 1:
        return RuleResult(
            rule_id="existing_structures",
            rule_category="deployment",
            result=RESULT_PASS,
            explanation="One existing structure. Standard siting scenario.",
            assumptions_used={"existing_structure_count": 1},
            confidence=0.8,
        )
    else:
        return RuleResult(
            rule_id="existing_structures",
            rule_category="deployment",
            result=RESULT_CONDITIONAL,
            explanation=f"{structure_count} existing structures on parcel. Siting is more complex — review for available space.",
            assumptions_used={"existing_structure_count": structure_count},
            confidence=0.65,
        )
