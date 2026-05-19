from app.engine.rules.base import RuleResult

DEFAULT_PROFILE = {
    "profile_id": "default",
    "categories": {
        # PRIMARY: zoning and ADU rules — multifamily/permissive zones score highest
        "zoning_compatibility": {
            "weight": 0.30,
            "rules": ["use_allowed", "adu_permitted"],
        },
        # PRIMARY: lot size, coverage, buildable envelope — plenty of land + small house
        "dimensional_fit": {
            "weight": 0.30,
            "rules": ["min_lot_size", "adu_max_size", "lot_coverage", "buildable_envelope"],
        },
        # SECONDARY: site access, overlay constraints, terrain
        "siting_likelihood": {
            "weight": 0.15,
            "rules": ["access_likely", "overlay_constraints", "slope_buildability"],
        },
        # SECONDARY: septic/sewer assumptions
        "septic_confidence": {
            "weight": 0.12,
            "rules": ["septic_capacity", "sewer_available"],
        },
        # TERTIARY: delivery and deployment logistics
        "deployment_ease": {
            "weight": 0.08,
            "rules": ["delivery_access", "electrical_service"],
        },
        # TERTIARY: outreach priority signals
        "outreach_attractiveness": {
            "weight": 0.05,
            "rules": ["existing_structures"],
        },
    },
    "tier_thresholds": {
        # Tier 1 (Green):  ≥85 — plenty of land, permissive zoning, ADU allowed, Plinth fits
        # Tier 2 (Yellow): ≥65 — feasible but tighter lot, some constraints
        # Tier 3 (Orange): ≥40 — conditional or marginal, manual review needed
        # Tier 4 (Red):    <40 — hard blocked: ADU banned, lot too small, setbacks prevent fit
        "tier_1": 85,
        "tier_2": 65,
        "tier_3": 40,
        "tier_4": 0,
    },
    "rule_scores": {
        "pass": 100,
        "conditional": 55,
        "fail": 0,
        "unknown": 30,
    },
    # Rules where a 'fail' hard-caps the parcel to Tier 4 (Red) regardless of other scores.
    # adu_permitted fail     = local regs prohibit ADU → Red (regulatory dead-end)
    # overlay_constraints    = overlay prohibition → Red
    # NOTE: buildable_envelope is NOT a hard block — geometry calc failures should not
    # nuke an otherwise viable parcel. It contributes heavily to dimensional_fit score instead.
    "hard_block_rules": ["use_allowed", "adu_permitted", "overlay_constraints"],
}

SCORING_PROFILES = {
    "default": DEFAULT_PROFILE,
}


def score_parcel(rule_results: list[RuleResult], profile_id: str = "default") -> dict:
    """
    Compute a composite score and tier from a list of RuleResult objects.

    Returns a dict with:
        score, tier, score_breakdown, confidence, blockers
    """
    profile = SCORING_PROFILES.get(profile_id, DEFAULT_PROFILE)
    rule_score_map = profile["rule_scores"]
    categories = profile["categories"]
    tier_thresholds = profile["tier_thresholds"]
    hard_block_rules = profile.get("hard_block_rules", [])

    # Index rule results by rule_id
    results_by_id: dict[str, RuleResult] = {r.rule_id: r for r in rule_results}

    category_scores = {}
    all_confidences = []
    unknown_count = 0
    total_rules = 0
    blockers = []
    hard_blocked = False

    for category_name, cat_config in categories.items():
        cat_rules = cat_config["rules"]
        cat_raw_scores = []

        for rule_id in cat_rules:
            total_rules += 1
            result = results_by_id.get(rule_id)
            if result is None:
                cat_raw_scores.append(rule_score_map["unknown"])
                unknown_count += 1
                all_confidences.append(0.0)
                continue

            raw_score = rule_score_map.get(result.result, rule_score_map["unknown"])
            cat_raw_scores.append(raw_score)
            all_confidences.append(result.confidence)

            if result.result == "unknown":
                unknown_count += 1

            if result.result == "fail" and rule_id in hard_block_rules:
                hard_blocked = True
                blockers.append({
                    "rule_id": rule_id,
                    "explanation": result.explanation,
                })
            elif result.result == "fail":
                blockers.append({
                    "rule_id": rule_id,
                    "explanation": result.explanation,
                })

        cat_avg = sum(cat_raw_scores) / len(cat_raw_scores) if cat_raw_scores else 0
        category_scores[category_name] = {
            "score": round(cat_avg, 1),
            "weight": cat_config["weight"],
            "rules_evaluated": len(cat_raw_scores),
        }

    # Weighted composite score
    composite = sum(
        cat["score"] * cat["weight"]
        for cat in category_scores.values()
    )
    composite = round(composite, 1)

    # Confidence: mean of all rule confidences, penalized for high unknown ratio
    avg_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
    unknown_ratio = unknown_count / total_rules if total_rules else 0
    if unknown_ratio > 0.3:
        avg_confidence = avg_confidence * (1 - (unknown_ratio - 0.3))
    confidence = round(max(0.0, min(1.0, avg_confidence)), 3)

    # Tier assignment.
    # A hard-block rule fires for things like ADU prohibition or a hard-block
    # overlay (FEMA SFHA, conservation restriction). Historically that forced
    # Tier 4 unconditionally — but a parcel that scores ≥70 on every other
    # axis is still a real lead, the constraint is just one factor the analyst
    # has to work around. Force Tier 4 only when the score itself is also weak.
    # The constraint is still visible in the `blockers` list either way.
    if hard_blocked and composite < 70:
        tier = 4
    elif composite >= tier_thresholds["tier_1"]:
        tier = 1
    elif composite >= tier_thresholds["tier_2"]:
        tier = 2
    elif composite >= tier_thresholds["tier_3"]:
        tier = 3
    else:
        tier = 4

    return {
        "score": composite,
        "tier": tier,
        "score_breakdown": category_scores,
        "confidence": confidence,
        "blockers": blockers,
        "scoring_profile": profile_id,
        "hard_blocked": hard_blocked,
    }


def evaluate_template_fits(rule_results: list[RuleResult], templates: list[dict]) -> list[dict]:
    """Return a summary of which templates fit based on rule results."""
    results_by_id = {r.rule_id: r for r in rule_results}
    adu_size_result = results_by_id.get("adu_max_size")

    fits = []
    for template in templates:
        fit_status = "unknown"
        notes = ""

        if adu_size_result:
            if adu_size_result.result == "pass":
                fit_status = "fits"
                notes = "Within ADU size limit"
            elif adu_size_result.result == "conditional":
                assumptions = adu_size_result.assumptions_used or {}
                fitting_templates = assumptions.get("fitting_templates", [])
                if template["template_id"] in fitting_templates:
                    fit_status = "fits"
                    notes = "Within ADU size limit"
                else:
                    fit_status = "does_not_fit"
                    notes = "Exceeds ADU max size for this district"
            elif adu_size_result.result == "fail":
                fit_status = "does_not_fit"
                notes = "No templates fit within ADU max size"

        fits.append({
            "template_id": template["template_id"],
            "template_name": template.get("template_name", ""),
            "footprint_area_sqft": template.get("footprint_area_sqft"),
            "fit_status": fit_status,
            "notes": notes,
        })

    return fits
