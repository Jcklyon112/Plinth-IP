"""
Engine runner: orchestrates rules evaluation and scoring for a single parcel.

Zoning code normalization:
  Raw GIS zoning codes are mapped to config district keys via the municipality
  config's 'zoning_code_map'. This makes the engine work with any GIS source
  regardless of how zoning codes are labeled in the source data.

Geometry:
  The parcel dict may contain 'geometry_shapely' (a Shapely geometry in WGS84)
  and 'calc_epsg' (integer EPSG for local projection). These are used by the
  buildable_envelope_rule for setback-accurate feasibility calculations.
"""
from app.engine.rules import (
    min_lot_size_rule, adu_max_size_rule, lot_coverage_rule, buildable_envelope_rule,
    use_allowed_rule, adu_permitted_rule,
    overlay_constraints_rule, access_likely_rule, slope_buildability_rule, electrical_service_rule,
    sewer_available_rule, septic_capacity_rule,
    delivery_access_rule, existing_structures_rule,
)
from app.engine.rules.defaults import apply_district_defaults
from app.engine.scoring import score_parcel, evaluate_template_fits


def normalize_zoning_code(raw_code: str | None, config: dict) -> str | None:
    """
    Map a raw GIS zoning code to the internal district key defined in the config.

    The config's 'zoning_code_map' handles variations in how municipalities label
    their zones in their GIS data (e.g. "R2" vs "R-2" vs "RES-2").

    Returns None if the code maps to a non-residential or unknown district.
    If no zoning_code_map is defined, the raw code is used directly.
    """
    if not raw_code:
        return None
    raw = str(raw_code).strip()
    zoning_map = config.get("zoning_code_map")
    if not zoning_map:
        # No map defined — use raw code directly (works if GIS codes match config keys)
        return raw
    # Explicit null in the map means "not a relevant district"
    if raw in zoning_map:
        return zoning_map[raw]  # may be None (non-residential)
    # Not in map → unknown district
    return None


def get_district_config(parcel: dict, config: dict) -> tuple[dict, str | None]:
    """
    Retrieve the zoning district config for a parcel.

    Returns (district_config_dict, normalized_zoning_code).
    district_config is empty dict if no match found.
    """
    raw_code = parcel.get("zoning_code")
    districts = config.get("zoning_districts", {})
    normalized = normalize_zoning_code(raw_code, config)
    if not normalized:
        return {}, normalized
    return districts.get(normalized, {}), normalized


# ---------------------------------------------------------------------------
# State-isolated land use code maps — NEVER cross-contaminate between states.
# NY code 105 = Agricultural (None), MA code 105 = three_family.
# ---------------------------------------------------------------------------

STATE_USE_CODE_MAPS = {
    "MA": {
        # MA 3-digit codes (from 4-digit MassGIS USE_CODE truncated)
        "101": "single_family",
        "102": "single_family",
        "103": "single_family",
        "104": "two_family",
        "105": "three_family",
        "106": "residential",
        "107": "residential",
        "108": "residential",
        "109": "residential",
        "111": "residential",
        "112": "residential",
        "113": "residential",
        "121": "residential",
        "122": "residential",
        "123": "residential",
        "124": "residential",
        "125": "residential",
        "126": "residential",
        "127": "residential",
        "128": "residential",
        "129": "residential",
        "130": "residential",
        "131": "residential",
        "132": "residential",
        # 4-digit originals (before truncation)
        "1010": "single_family",
        "1020": "single_family",
        "1030": "single_family",
        "1040": "two_family",
        "1050": "three_family",
        "1060": "residential",
        "1110": "residential",
        "1120": "residential",
        "1300": "residential",
    },
    "NY": {
        # ── NYC Tax Class codes (1-2 digit) ───────────────────────────
        "01": "single_family",       # 1-3 family residential
        "1":  "single_family",
        "02": "residential",         # 4+ family / co-op
        "2":  "residential",
        "03": None,                  # Utility
        "3":  None,
        "04": None,                  # Commercial/industrial
        "4":  None,
        "05": "residential",         # Condos (often residential)
        "5":  "residential",
        "06": "residential",         # Residential condos
        "6":  "residential",
        "07": None,                  # Commercial condos
        "7":  None,
        "08": "residential",         # Mixed use (has residential)
        "8":  "residential",
        "09": None,                  # Utility condos
        "9":  None,
        "10": None,                  # Vacant land
        "11": "residential",         # Residential condos
        # ── NY State RPTL property class codes — 3 digit ──────────────
        # 100s = Agricultural — NOT residential
        "100": None, "101": None, "102": None, "103": None, "104": None,
        "105": None, "106": None, "107": None, "108": None, "110": None,
        "111": None, "112": None, "113": None, "114": None, "120": None,
        "130": None, "140": None, "150": None, "151": None, "152": None,
        "160": None, "170": None, "180": None, "190": None,
        # 200s = Residential
        "210": "single_family",
        "215": "single_family",
        "220": "two_family",
        "230": "residential",
        "240": "single_family",
        "241": "single_family",
        "242": "single_family",
        "250": "single_family",
        "260": "residential",
        "270": "single_family",
        "280": "residential",
        "281": "single_family",
        "283": "residential",
        # 300s+ = Non-residential
        "300": None, "311": None, "312": None, "314": None, "322": None,
        "330": None, "340": None, "400": None, "411": None, "421": None,
        "432": None, "449": None, "464": None, "480": None, "500": None,
        "600": None, "610": None, "620": None, "651": None, "695": None,
        "710": None, "720": None, "800": None, "900": None,
    },
    "NH": {
        "101": "single_family", "102": "single_family",
        "111": "residential", "112": "two_family", "113": "residential",
        "120": "residential", "130": "residential",
    },
    "NJ": {
        "2": "single_family",
        "3A": "single_family", "3B": "two_family", "3C": "residential",
        "4A": None, "4B": None, "4C": None,
        "15A": None, "15B": None, "15C": None, "15D": None, "15E": None, "15F": None,
    },
    # FL Department of Revenue (DOR) use codes — 2-digit
    "FL": {
        "01": "single_family",       # Single-family residential
        "02": "mobile_home",         # Mobile home
        "03": "residential",         # Multi-family (10+ units)
        "04": "residential",         # Condominium
        "05": "residential",         # Cooperatives
        "06": "residential",         # Retirement home (privately operated)
        "07": "residential",         # Miscellaneous residential
        "08": "residential",         # Multi-family (less than 10 units)
        "09": "vacant",              # Vacant residential land
        "10": None,                  # Vacant commercial land
        "11": None,                  # Stores (one story)
        "12": None,                  # Mixed use
        "13": None,                  # Department stores
        "14": None,                  # Supermarkets
        "15": None,                  # Regional shopping centers
        "16": None,                  # Community shopping centers
        "17": None,                  # Office buildings, non-professional
        "18": None,                  # Office buildings, professional
        "19": None,                  # Professional service building
        "20": None,                  # Airports (private)
        "21": None,                  # Restaurants, cafeterias
        "22": None,                  # Drive-in restaurants
        "23": None,                  # Financial institutions
        "24": None,                  # Insurance company offices
        "25": None,                  # Service stations
        "26": None,                  # Auto sales, repair
        "27": None,                  # Parking lots
        "28": None,                  # Wholesale outlets
        "29": None,                  # Wholesale (non-food goods)
        "30": None,                  # Drive-in theaters
        "31": None,                  # Open stadiums
        "32": None,                  # Race tracks
        "33": None,                  # Golf courses, driving ranges
        "34": None,                  # Hotels, motels
        "38": None,                  # Golf courses (private clubs)
        "39": None,                  # Miscellaneous commercial
        "40": None,                  # Vacant industrial
        "41": None,                  # Light manufacturing
        "42": None,                  # Heavy industrial
        "48": None,                  # Warehousing, distribution
        "49": None,                  # Open storage
        "50": None,                  # Improved agricultural
        "51": None,                  # Cropland (cash grain)
        "52": None,                  # Poultry / eggs
        "53": None,                  # Field crops
        "54": None,                  # Livestock
        "55": None,                  # Timberland
        "60": None,                  # Grazing land
        "66": None,                  # Non-agricultural acreage
        "67": None,                  # Fruit, nut bearing
        "68": None,                  # Ornamentals, miscellaneous
        "69": None,                  # Ornamentals
        "70": None,                  # Vacant (unmapped)
        "71": None,                  # Churches
        "72": None,                  # Private schools
        "73": None,                  # Private hospitals
        "74": None,                  # Homes for the aged
        "75": None,                  # Orphanages
        "76": None,                  # Mortuaries, cemeteries
        "77": None,                  # Clubs, lodges
        "78": None,                  # Recreational clubs
        "79": None,                  # Cultural organizations
        "80": None,                  # Governmental
        "81": None,                  # Military
        "82": None,                  # Forest/park
        "83": None,                  # Public county schools
        "84": None,                  # Colleges
        "85": None,                  # Hospitals
        "86": None,                  # Counties
        "87": None,                  # State
        "88": None,                  # Federal
        "89": None,                  # Municipal
        "90": None,                  # Leasehold interests
        "91": None,                  # Utility, privately owned
        "92": None,                  # Right-of-way
        "93": None,                  # Subsurface rights only
        "94": None,                  # Outdoor recreational
        "95": None,                  # Rivers, lakes
        "99": None,                  # Acreage not zoned for agriculture
    },
    # Illinois Cook County property class codes
    "IL": {
        "2-00": "single_family",     # Single-family detached (no garage)
        "2-01": "single_family",     # Single-family detached (with garage)
        "2-02": "single_family",     # Single-family (with garage/commercial use)
        "2-03": "two_family",        # Two-family (with garage/commercial use)
        "2-04": "two_family",        # Two-flat or two-family
        "2-05": "two_family",        # Two-flat (with 3rd unit in garage)
        "2-06": "residential",       # Three-flat or multi
        "2-07": "residential",       # Four+ apartments
        "2-08": "residential",       # Mixed use (stores + apts)
        "2-09": "residential",       # Mixed use commercial/residential
        "2-10": "residential",       # Old style row house
        "2-11": "residential",       # Townhouse
        "2-12": "residential",       # Condominium
        "3-00": None,                # Vacant land
        "4-00": None,                # Commercial
        "5-00": None,                # Industrial
        "6-00": None,                # Exempt
        # Short codes without dashes
        "200": "single_family",
        "201": "single_family",
        "202": "single_family",
        "203": "two_family",
        "204": "two_family",
        "205": "two_family",
        "206": "residential",
        "207": "residential",
        "208": "residential",
        "209": "residential",
        "210": "residential",
        "211": "residential",
        "212": "residential",
        "300": None,
        "400": None,
        "500": None,
        "600": None,
    },
    # PA uses county-level class codes; common statewide residential codes below
    "PA": {
        # Residential
        "R": "single_family",       # Generic residential (many counties)
        "R1": "single_family",
        "R2": "two_family",
        "R3": "residential",
        "A": "single_family",       # Some counties use A = residential
        "1": "single_family",       # Numeric class 1 = residential in many PA counties
        "2": "two_family",
        "3": "residential",
        "10": "single_family",      # Class 10 = res in e.g. Monroe/Pike County
        "11": "single_family",
        "12": "two_family",
        "13": "residential",
        "14": "residential",
        "15": "residential",
        "20": "commercial",
        "21": "commercial",
        "30": None,                 # Agricultural
        "31": None,
        "40": None,                 # Exempt
        "41": None,
        # Generic fallback codes
        "RES": "single_family",
        "COM": None,
        "IND": None,
        "AGR": None,
        "EXM": None,
        "VAC": None,
    },
}


def normalize_land_use_type(parcel: dict, config: dict, state_code: str = "") -> dict:
    """
    Normalize land_use_type using state-specific code maps.

    Each state has its own code system — codes MUST NOT leak across states.
    NY code 105 = Agricultural (None), MA code 105 = three_family.
    """
    raw = (parcel.get("land_use_type") or "").strip()
    if not raw:
        return parcel

    # Already a named type (e.g. "single_family") — no change needed
    if raw.isalpha():
        return parcel
    if not any(c.isdigit() for c in raw):
        return parcel

    # Determine state from parameter, config, or municipality_id prefix
    state = (
        state_code
        or config.get("state", "")
        or parcel.get("municipality_id", "")[:2].upper()
    ).upper()

    code_map = STATE_USE_CODE_MAPS.get(state, {})
    if not code_map:
        # No state map — leave as-is
        return parcel

    # Try exact match first, then strip leading zeros
    mapped = code_map.get(raw)
    if mapped is None and raw not in code_map:
        # Try 3-digit truncation of 4-digit codes
        short = raw[:3] if len(raw) == 4 else raw
        mapped = code_map.get(short)

    if mapped is not None or raw in code_map:
        parcel = dict(parcel)
        parcel["land_use_type"] = mapped  # None = non-residential
    return parcel


def evaluate_parcel(parcel: dict, config: dict, templates: list[dict] | None = None) -> dict:
    """
    Run all rules and scoring for a single parcel.

    Returns the parcel dict enriched with:
      - rule_results: dict of rule_name -> RuleResult
      - score: composite feasibility score (0-100)
      - tier: 1-4
      - score_breakdown: per-category scores
      - template_fits: list of template fit results
    """
    state_code = config.get("state", parcel.get("municipality_id", "")[:2].upper())

    # Normalize land use type using state-specific codes
    parcel = normalize_land_use_type(parcel, config, state_code)

    # Normalize zoning code via config map
    district_config, normalized_zoning = get_district_config(parcel, config)

    # Detect non-residential parcels: zoning_code_map explicitly maps to None
    raw_zoning = parcel.get("zoning_code")
    zoning_map = config.get("zoning_code_map", {})
    is_non_residential = (
        raw_zoning is not None
        and str(raw_zoning).strip() in zoning_map
        and zoning_map[str(raw_zoning).strip()] is None
    )

    # Fill missing district config values with sensible aggregates so rules
    # produce real PASS/FAIL/CONDITIONAL outcomes instead of N/A. Rules that
    # consume an assumed key MUST mark their explanation with `*`.
    # We skip defaults for parcels classified non-residential — those should
    # short-circuit on use/ADU rules anyway.
    if not is_non_residential:
        district_config = apply_district_defaults(district_config)

    # Inject normalized zoning + human-readable label back into parcel for rules
    parcel = dict(parcel)
    if is_non_residential:
        parcel["zoning_district"] = None
        parcel["zoning_district_label"] = "Non-Residential"
        parcel["non_residential"] = True
    elif normalized_zoning:
        parcel["zoning_district"] = normalized_zoning
        parcel["zoning_district_label"] = district_config.get("label") or normalized_zoning

    # ── Run all rules ────────────────────────────────────────────────────
    rule_results = {}

    tpl = templates or []

    # Dimensional
    rule_results["min_lot_size"]       = min_lot_size_rule(parcel, district_config)
    rule_results["adu_max_size"]       = adu_max_size_rule(parcel, district_config, tpl)
    rule_results["lot_coverage"]       = lot_coverage_rule(parcel, district_config, tpl)
    rule_results["buildable_envelope"] = buildable_envelope_rule(parcel, district_config)

    # Use
    rule_results["use_allowed"]   = use_allowed_rule(parcel, district_config)
    rule_results["adu_permitted"] = adu_permitted_rule(parcel, district_config, config)

    # Physical
    rule_results["overlay_constraints"]  = overlay_constraints_rule(parcel, config)
    rule_results["access_likely"]        = access_likely_rule(parcel, district_config)
    rule_results["slope_buildability"]   = slope_buildability_rule(parcel, district_config)

    # Septic / sewer
    rule_results["sewer_available"]  = sewer_available_rule(parcel, config)
    rule_results["septic_capacity"]  = septic_capacity_rule(parcel, config, tpl)

    # Deployment
    rule_results["delivery_access"]     = delivery_access_rule(parcel, district_config)
    rule_results["existing_structures"] = existing_structures_rule(parcel, district_config)
    rule_results["electrical_service"]  = electrical_service_rule(parcel, district_config)

    # ── Score ────────────────────────────────────────────────────────────
    score_result = score_parcel(list(rule_results.values()))

    # ── Template fits ────────────────────────────────────────────────────
    template_fits = []
    if templates:
        template_fits = evaluate_template_fits(list(rule_results.values()), templates)

    return {
        **parcel,
        "rule_results": rule_results,
        "score": score_result["score"],
        "tier": score_result["tier"],
        "confidence": score_result["confidence"],
        "score_breakdown": score_result["score_breakdown"],
        "blockers": score_result["blockers"],
        "template_fits": template_fits,
    }