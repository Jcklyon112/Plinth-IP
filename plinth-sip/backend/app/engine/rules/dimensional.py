from app.engine.rules.base import RuleResult, RESULT_PASS, RESULT_CONDITIONAL, RESULT_FAIL, RESULT_UNKNOWN
from app.engine.rules.defaults import was_assumed, mark, adjust_confidence

# Standard Plinth unit footprint — always 15ft x 35ft, either orientation
PLINTH_WIDTH_FT = 15.0
PLINTH_DEPTH_FT = 35.0
PLINTH_AREA_SQFT = PLINTH_WIDTH_FT * PLINTH_DEPTH_FT  # 525 sqft
FT_PER_METER = 3.28084
SQFT_PER_SQM = 10.7639


def min_lot_size_rule(parcel: dict, district_config: dict) -> RuleResult:
    """Check whether the parcel meets the district minimum lot size."""
    lot_area = parcel.get("lot_area_sqft")
    min_area = district_config.get("min_lot_area_sqft")
    confidence = district_config.get("confidence", 1.0)

    if lot_area is None:
        return RuleResult(
            rule_id="min_lot_size",
            rule_category="dimensional",
            result=RESULT_UNKNOWN,
            explanation="Lot area not available in source data. Cannot evaluate minimum lot size.",
            assumptions_used={"min_lot_area_sqft": min_area},
            confidence=0.0,
        )

    assumed = was_assumed(district_config, "min_lot_area_sqft")
    assumptions = {"min_lot_area_sqft": min_area, "assumed_default": assumed}

    if lot_area >= min_area:
        explanation = f"Lot area {lot_area:,.0f} sqft meets minimum {min_area:,.0f} sqft for this district."
    else:
        explanation = f"Lot area {lot_area:,.0f} sqft is below the minimum {min_area:,.0f} sqft for this district."

    if assumed:
        explanation = mark(explanation)
        confidence = adjust_confidence(confidence)

    return RuleResult(
        rule_id="min_lot_size",
        rule_category="dimensional",
        result=RESULT_PASS if lot_area >= min_area else RESULT_FAIL,
        explanation=explanation,
        assumptions_used=assumptions,
        confidence=confidence,
    )


def adu_max_size_rule(parcel: dict, district_config: dict, templates: list[dict]) -> RuleResult:
    """Check whether any active Plinth template fits within the district ADU max size."""
    adu_max = district_config.get("adu_max_sqft")
    confidence = district_config.get("confidence", 1.0)

    if adu_max is None:
        return RuleResult(
            rule_id="adu_max_size",
            rule_category="dimensional",
            result=RESULT_UNKNOWN,
            explanation="ADU maximum size not defined in config for this district.",
            assumptions_used={},
            confidence=0.0,
        )

    assumed = was_assumed(district_config, "adu_max_sqft")
    if assumed:
        confidence = adjust_confidence(confidence)

    fitting = [t for t in templates if t.get("footprint_area_sqft", 9999) <= adu_max]
    all_fit = len(fitting) == len(templates)
    some_fit = len(fitting) > 0

    if all_fit:
        explanation = f"All active Plinth templates fit within ADU max size of {adu_max} sqft."
        result = RESULT_PASS
        assumptions = {"adu_max_sqft": adu_max, "assumed_default": assumed}
    elif some_fit:
        fitting_names = [t["template_id"] for t in fitting]
        explanation = f"Some templates fit within ADU max {adu_max} sqft: {fitting_names}. Others exceed limit."
        result = RESULT_CONDITIONAL
        assumptions = {"adu_max_sqft": adu_max, "fitting_templates": fitting_names, "assumed_default": assumed}
    else:
        explanation = f"No active Plinth templates fit within ADU max size of {adu_max} sqft."
        result = RESULT_FAIL
        assumptions = {"adu_max_sqft": adu_max, "assumed_default": assumed}

    if assumed:
        explanation = mark(explanation)

    return RuleResult(
        rule_id="adu_max_size",
        rule_category="dimensional",
        result=result,
        explanation=explanation,
        assumptions_used=assumptions,
        confidence=confidence,
    )


def lot_coverage_rule(parcel: dict, district_config: dict, templates: list[dict]) -> RuleResult:
    """
    Estimate whether adding the smallest Plinth template would exceed max lot coverage.
    Uses existing_building_footprint_area if available; assumes 0 if not (optimistic).
    """
    lot_area = parcel.get("lot_area_sqft")
    existing_footprint = parcel.get("existing_building_footprint_area")
    max_coverage_pct = district_config.get("max_lot_coverage_pct")
    confidence = district_config.get("confidence", 1.0)

    if lot_area is None or max_coverage_pct is None:
        return RuleResult(
            rule_id="lot_coverage",
            rule_category="dimensional",
            result=RESULT_UNKNOWN,
            explanation="Cannot evaluate lot coverage: lot area missing.",
            assumptions_used={},
            confidence=0.0,
        )

    coverage_assumed = was_assumed(district_config, "max_lot_coverage_pct")
    if coverage_assumed:
        confidence = adjust_confidence(confidence)

    smallest_template = min(templates, key=lambda t: t.get("footprint_area_sqft", 9999), default=None)
    if smallest_template is None:
        return RuleResult(
            rule_id="lot_coverage",
            rule_category="dimensional",
            result=RESULT_UNKNOWN,
            explanation="No active Plinth templates to evaluate.",
            assumptions_used={},
            confidence=0.0,
        )

    assumed_existing = existing_footprint if existing_footprint is not None else 0.0
    footprint_assumption = "from assessor data" if existing_footprint is not None else "assumed 0 (missing data)"
    added_footprint = smallest_template["footprint_area_sqft"]
    total = assumed_existing + added_footprint
    resulting_coverage = total / lot_area
    max_coverage = max_coverage_pct

    margin = max_coverage - resulting_coverage
    within_10pct = abs(margin) <= 0.10

    common_assumptions = {
        "existing_footprint": assumed_existing,
        "footprint_assumption": footprint_assumption,
        "template_id": smallest_template["template_id"],
        "max_lot_coverage_pct": max_coverage,
        "assumed_default": coverage_assumed,
    }

    if resulting_coverage <= max_coverage and not within_10pct:
        explanation = (
            f"Adding smallest template ({added_footprint} sqft) to existing footprint "
            f"({assumed_existing} sqft, {footprint_assumption}) yields {resulting_coverage:.1%} coverage, "
            f"within max {max_coverage:.0%}."
        )
        result = RESULT_PASS
        out_conf = confidence if existing_footprint is not None else confidence * 0.6
    elif resulting_coverage <= max_coverage:
        explanation = (
            f"Resulting coverage {resulting_coverage:.1%} is within max {max_coverage:.0%} but tight (within 10%). "
            f"Physical siting may push coverage over limit."
        )
        result = RESULT_CONDITIONAL
        out_conf = confidence * 0.7
    else:
        explanation = (
            f"Adding smallest template would result in {resulting_coverage:.1%} coverage, "
            f"exceeding max {max_coverage:.0%}."
        )
        result = RESULT_FAIL
        out_conf = confidence if existing_footprint is not None else confidence * 0.6

    if coverage_assumed:
        explanation = mark(explanation)

    return RuleResult(
        rule_id="lot_coverage",
        rule_category="dimensional",
        result=result,
        explanation=explanation,
        assumptions_used=common_assumptions,
        confidence=out_conf,
    )


def _directional_buildable_envelope(projected_geom, front_m: float, rear_m: float, side_m: float):
    """
    Compute a directional-setback buildable envelope.

    Approximates the parcel by its minimum-rotated bounding rectangle.
    Convention (matches typical residential lots): the LONG axis is the
    front-to-rear depth, the SHORT axis is the side-to-side frontage.
      - front + rear setbacks reduce the LONG axis
      - side setbacks reduce the SHORT axis (each side)
    Returns (envelope_polygon, env_long_m, env_short_m) in meters, or
    (None, 0, 0) if setbacks eliminate the envelope.

    Without road-adjacency data we can't tell which long end is the actual
    street side. That doesn't matter for fit-checking: total long-axis
    dimension is `long_len - front_m - rear_m` regardless of orientation.
    The envelope's center shifts by `(rear_m - front_m)/2` along the long
    axis if the two setbacks differ.

    For corner lots and irregular parcels this rectangular approximation
    can be wrong about which axis is "depth" — but it is consistently
    closer to reality than averaging all four setbacks into one buffer.
    """
    import math
    from shapely.geometry import Polygon

    rect = projected_geom.minimum_rotated_rectangle
    if rect is None or rect.is_empty:
        return None, 0.0, 0.0

    coords = list(rect.exterior.coords)
    if len(coords) < 5:
        return None, 0.0, 0.0
    pts = coords[:4]

    # Compute the four edge vectors (in order around the rectangle)
    edges = [(pts[(i + 1) % 4][0] - pts[i][0], pts[(i + 1) % 4][1] - pts[i][1]) for i in range(4)]
    lens = [math.hypot(ex, ey) for ex, ey in edges]

    # Identify short vs long axes by opposite-edge length sum
    if lens[0] + lens[2] >= lens[1] + lens[3]:
        long_idx, short_idx = 0, 1
    else:
        long_idx, short_idx = 1, 0

    long_len = lens[long_idx]    # front-to-rear depth
    short_len = lens[short_idx]  # side-to-side frontage

    if short_len <= 0 or long_len <= 0:
        return None, 0.0, 0.0

    # Unit vectors: u along long axis (front/rear), v along short axis (side/side)
    lex, ley = edges[long_idx]
    sex, sey = edges[short_idx]
    u = (lex / long_len, ley / long_len)
    v = (sex / short_len, sey / short_len)

    cx = sum(p[0] for p in pts) / 4.0
    cy = sum(p[1] for p in pts) / 4.0

    # Directional shrink
    new_long_total = long_len - front_m - rear_m
    new_short_total = short_len - 2.0 * side_m
    if new_long_total <= 0 or new_short_total <= 0:
        return None, 0.0, 0.0

    half_long = new_long_total / 2.0
    half_short = new_short_total / 2.0

    # Center shifts along the long axis toward whichever end has smaller setback.
    # Convention: if rear_m > front_m, the buildable area sits closer to the front.
    shift = (rear_m - front_m) / 2.0
    nx = cx + u[0] * shift
    ny = cy + u[1] * shift

    corners = [
        (nx - u[0] * half_long - v[0] * half_short, ny - u[1] * half_long - v[1] * half_short),
        (nx + u[0] * half_long - v[0] * half_short, ny + u[1] * half_long - v[1] * half_short),
        (nx + u[0] * half_long + v[0] * half_short, ny + u[1] * half_long + v[1] * half_short),
        (nx - u[0] * half_long + v[0] * half_short, ny - u[1] * half_long + v[1] * half_short),
    ]
    return Polygon(corners), new_long_total, new_short_total


def buildable_envelope_rule(parcel: dict, district_config: dict) -> RuleResult:
    """
    Compute the setback-adjusted buildable envelope and test whether a 15×35 Plinth
    unit fits in either orientation.

    Method: project parcel polygon to the municipality's local meters CRS, then
    apply DIRECTIONAL setbacks via the parcel's minimum-rotated bounding rectangle:
      - front + rear setbacks reduce the short axis
      - side setbacks reduce the long axis (each side)
    This is far more accurate than a uniform-average buffer for narrow/deep lots
    where front (often 30–50 ft) and side (often 10–15 ft) setbacks differ
    significantly.
    """
    geom = parcel.get("geometry_shapely")
    setbacks = district_config.get("setbacks", {})

    if geom is None:
        return RuleResult(
            rule_id="buildable_envelope",
            rule_category="dimensional",
            result=RESULT_UNKNOWN,
            explanation="No parcel geometry available. Cannot compute setback-adjusted buildable envelope.",
            assumptions_used={"plinth_footprint_sqft": PLINTH_AREA_SQFT},
            confidence=0.0,
        )

    if not setbacks:
        return RuleResult(
            rule_id="buildable_envelope",
            rule_category="dimensional",
            result=RESULT_UNKNOWN,
            explanation="No setbacks defined in district config. Cannot compute buildable envelope.",
            assumptions_used={"plinth_footprint_sqft": PLINTH_AREA_SQFT},
            confidence=0.0,
        )

    setbacks_assumed = was_assumed(district_config, "setbacks")

    front_ft = setbacks.get("front_ft", 0) or 0
    rear_ft = setbacks.get("rear_ft", 0) or 0
    side_ft = setbacks.get("side_ft", 0) or 0

    front_m = front_ft / FT_PER_METER
    rear_m = rear_ft / FT_PER_METER
    side_m = side_ft / FT_PER_METER

    calc_epsg = parcel.get("calc_epsg", 26986)

    try:
        import pyproj
        from shapely.ops import transform as shapely_transform

        transformer = pyproj.Transformer.from_crs(
            "EPSG:4326", f"EPSG:{calc_epsg}", always_xy=True
        )
        projected = shapely_transform(transformer.transform, geom)

        envelope, env_long_m, env_short_m = _directional_buildable_envelope(
            projected, front_m, rear_m, side_m
        )

        common_assumptions = {
            "front_ft": front_ft, "rear_ft": rear_ft, "side_ft": side_ft,
            "plinth_footprint_sqft": PLINTH_AREA_SQFT,
            "calc_epsg": calc_epsg,
            "method": "directional_min_rotated_rect",
            "assumed_default": setbacks_assumed,
        }

        def _finalize(result, explanation, conf):
            return RuleResult(
                rule_id="buildable_envelope",
                rule_category="dimensional",
                result=result,
                explanation=mark(explanation) if setbacks_assumed else explanation,
                assumptions_used=common_assumptions,
                confidence=adjust_confidence(conf) if setbacks_assumed else conf,
            )

        if envelope is None or envelope.is_empty:
            return _finalize(
                RESULT_FAIL,
                (
                    f"Setbacks ({front_ft}ft front + {rear_ft}ft rear, {side_ft}ft side) "
                    "eliminate all buildable area on this parcel."
                ),
                0.9,
            )

        envelope_sqft = envelope.area * SQFT_PER_SQM
        env_long_ft = env_long_m * FT_PER_METER
        env_short_ft = env_short_m * FT_PER_METER

        # Plinth 15×35: shorter dimension must clear 15 ft, longer must clear 35 ft
        env_min_ft = min(env_long_ft, env_short_ft)
        env_max_ft = max(env_long_ft, env_short_ft)
        unit_fits = env_min_ft >= PLINTH_WIDTH_FT and env_max_ft >= PLINTH_DEPTH_FT

        common_assumptions.update({
            "buildable_area_sqft": round(envelope_sqft),
            "envelope_long_ft": round(env_long_ft, 1),
            "envelope_short_ft": round(env_short_ft, 1),
        })

        if unit_fits and envelope_sqft >= PLINTH_AREA_SQFT:
            return _finalize(
                RESULT_PASS,
                (
                    f"Buildable envelope after directional setbacks: {envelope_sqft:,.0f} sqft "
                    f"({env_long_ft:.0f}ft × {env_short_ft:.0f}ft). "
                    "Plinth 15×35 fits."
                ),
                0.9,
            )

        if envelope_sqft >= PLINTH_AREA_SQFT:
            return _finalize(
                RESULT_CONDITIONAL,
                (
                    f"Envelope area {envelope_sqft:,.0f} sqft is sufficient but shape "
                    f"({env_long_ft:.0f}ft × {env_short_ft:.0f}ft) does not cleanly accept "
                    "a 15×35 footprint. Site-specific siting study needed."
                ),
                0.6,
            )

        return _finalize(
            RESULT_FAIL,
            (
                f"Buildable envelope after directional setbacks: {envelope_sqft:,.0f} sqft "
                f"({env_long_ft:.0f}ft × {env_short_ft:.0f}ft) — smaller than 525 sqft Plinth footprint."
            ),
            0.85,
        )

    except Exception as e:
        return RuleResult(
            rule_id="buildable_envelope",
            rule_category="dimensional",
            result=RESULT_UNKNOWN,
            explanation=f"Geometry calculation error: {e}. Manual review required.",
            assumptions_used={"plinth_footprint_sqft": PLINTH_AREA_SQFT},
            confidence=0.0,
        )
