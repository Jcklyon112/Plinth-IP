from datetime import date

from app.engine.rules.base import RuleResult, RESULT_PASS, RESULT_CONDITIONAL, RESULT_FAIL, RESULT_UNKNOWN
from app.engine.rules.defaults import was_assumed, mark, adjust_confidence

RESIDENTIAL_USE_TYPES = {"single_family", "residential", "accessory", "two_family", "multi_family"}


def _matched_state_law_override(parcel: dict, config: dict | None) -> tuple[str, dict] | tuple[None, None]:
    """
    If a state-law override (e.g. MA Affordable Homes Act §8 ADU-by-right)
    applies to this parcel today, return (override_key, override_dict).

    A parcel matches if (a) its normalized land_use_type is in the override's
    applies_to_use_codes list AND (b) today's date is on/after effective_date.
    """
    if not config:
        return None, None
    overrides = config.get("state_law_overrides") or {}
    if not overrides:
        return None, None
    parcel_use = (parcel.get("land_use_type") or "").lower().strip()
    today = date.today()
    for key, ov in overrides.items():
        applies_to = [str(u).lower() for u in (ov.get("applies_to_use_codes") or [])]
        if not applies_to or parcel_use not in applies_to:
            continue
        eff = ov.get("effective_date")
        if eff:
            try:
                if today < date.fromisoformat(str(eff)[:10]):
                    continue
            except (ValueError, TypeError):
                pass
        return key, ov
    return None, None


def use_allowed_rule(parcel: dict, district_config: dict) -> RuleResult:
    """Check whether the parcel's land use type is permitted in the district."""
    # Early exit: non-residential parcels explicitly mapped to None in zoning_code_map
    if parcel.get("non_residential"):
        return RuleResult(
            rule_id="use_allowed",
            rule_category="use",
            result=RESULT_FAIL,
            explanation=f"Parcel is classified as non-residential (code: {parcel.get('zoning_code')}). ADU deployment not applicable.",
            assumptions_used={"zoning_code": parcel.get("zoning_code"), "classification": "non_residential"},
            confidence=0.8,
        )

    land_use = (parcel.get("land_use_type") or "").lower().strip()
    assessed_use = (parcel.get("assessed_use") or "").lower().strip()
    allowed_uses = [u.lower() for u in district_config.get("use_allowed", [])]
    confidence = district_config.get("confidence", 1.0)

    use_assumed = was_assumed(district_config, "use_allowed")

    check_value = land_use or assessed_use
    if not check_value:
        return RuleResult(
            rule_id="use_allowed",
            rule_category="use",
            result=RESULT_UNKNOWN,
            explanation="Parcel land use type not available. Cannot evaluate use allowance.",
            assumptions_used={"allowed_uses": allowed_uses, "assumed_default": use_assumed},
            confidence=0.0,
        )

    assumptions = {"allowed_uses": allowed_uses, "assumed_default": use_assumed}

    # Direct match
    if check_value in allowed_uses:
        explanation = f"Land use '{check_value}' is permitted in this district."
        result = RESULT_PASS
        out_conf = confidence
    elif check_value in RESIDENTIAL_USE_TYPES and any(u in RESIDENTIAL_USE_TYPES for u in allowed_uses):
        # Broad residential match
        explanation = (
            f"Land use '{check_value}' is residential. District allows {allowed_uses}. "
            "Likely compatible but confirm exact use classification."
        )
        result = RESULT_CONDITIONAL
        out_conf = confidence * 0.8
    else:
        explanation = f"Land use '{check_value}' does not appear to be permitted in this district (allowed: {allowed_uses})."
        result = RESULT_FAIL
        out_conf = confidence

    if use_assumed:
        explanation = mark(explanation)
        out_conf = adjust_confidence(out_conf)

    return RuleResult(
        rule_id="use_allowed",
        rule_category="use",
        result=result,
        explanation=explanation,
        assumptions_used=assumptions,
        confidence=out_conf,
    )


def adu_permitted_rule(parcel: dict, district_config: dict, config: dict | None = None) -> RuleResult:
    """Check whether accessory / ADU units are permitted in the district.

    State-law overrides (e.g. MA Affordable Homes Act §8 ADU-by-right
    effective 2025-02-02) take precedence over local district config when
    they apply to the parcel's land use type.
    """
    # Non-residential → ADU not applicable
    if parcel.get("non_residential"):
        return RuleResult(
            rule_id="adu_permitted",
            rule_category="use",
            result=RESULT_FAIL,
            explanation="Non-residential parcel. ADU not applicable.",
            assumptions_used={"classification": "non_residential"},
            confidence=0.8,
        )

    # ── State-law override check (e.g. MA AHA §8) ────────────────────────
    override_key, override = _matched_state_law_override(parcel, config)
    if override:
        law = override.get("law", "state law")
        descr = override.get("description") or ""
        return RuleResult(
            rule_id="adu_permitted",
            rule_category="use",
            result=RESULT_PASS,
            explanation=f"ADU permitted by-right under {law}. {descr}".strip(),
            assumptions_used={
                "state_law_override": override_key,
                "law": law,
                "effective_date": override.get("effective_date"),
                "supersedes_local": True,
            },
            confidence=float(override.get("confidence", 0.9)),
        )

    adu_allowed = district_config.get("adu_allowed")
    adu_notes = district_config.get("adu_notes") or ""
    confidence = district_config.get("confidence", 1.0)
    adu_assumed = was_assumed(district_config, "adu_allowed")

    if adu_allowed is None:
        return RuleResult(
            rule_id="adu_permitted",
            rule_category="use",
            result=RESULT_UNKNOWN,
            explanation="ADU allowance not defined in config for this district. Manual review required.",
            assumptions_used={},
            confidence=0.0,
        )

    if adu_allowed is True:
        note_suffix = f" Note: {adu_notes}" if adu_notes else ""
        explanation = f"ADU/accessory units are permitted in this district.{note_suffix}"
        result = RESULT_PASS
        out_conf = confidence
        assumptions = {"adu_allowed": True, "adu_notes": adu_notes, "assumed_default": adu_assumed}
    else:
        ambiguity_keywords = ["review", "variance", "special permit", "conditional", "unclear", "not addressed"]
        has_ambiguity = any(kw in adu_notes.lower() for kw in ambiguity_keywords)
        if has_ambiguity:
            explanation = f"ADU not clearly permitted. Notes indicate possible path: {adu_notes}"
            result = RESULT_CONDITIONAL
            out_conf = confidence * 0.5
            assumptions = {"adu_allowed": False, "adu_notes": adu_notes, "assumed_default": adu_assumed}
        else:
            explanation = f"ADU/accessory units are not permitted in this district. {adu_notes}"
            result = RESULT_FAIL
            out_conf = confidence
            assumptions = {"adu_allowed": False, "assumed_default": adu_assumed}

    if adu_assumed:
        explanation = mark(explanation)
        out_conf = adjust_confidence(out_conf)

    return RuleResult(
        rule_id="adu_permitted",
        rule_category="use",
        result=result,
        explanation=explanation,
        assumptions_used=assumptions,
        confidence=out_conf,
    )
