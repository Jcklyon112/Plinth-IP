"""
Auto-Config Generator
Generates a baseline municipality config from GIS parcel data.

This is Phase 5's "instant setup" — it creates a working config from:
  1. Municipality metadata (from Census geocoder)
  2. State defaults (setbacks, lot sizes, etc.)
  3. Discovered zoning codes (from the actual parcel data)

The generated config is deliberately conservative and clearly marked
as auto-generated with low confidence. Analysts should verify and
refine using the actual zoning ordinance.

This is NOT a replacement for proper zoning research — it's a starting
point that lets the system produce meaningful scores immediately while
allowing incremental improvement.
"""

import json
import os
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# State-level defaults
# These are median/typical values for common residential zoning in each state.
# They are conservative defaults — better to under-score than over-score.
# ---------------------------------------------------------------------------

STATE_DEFAULTS = {
    "MA": {
        "typical_min_lot_sf": 20000,    # ~0.5 acre (small town residential)
        "typical_setback_front": 30,
        "typical_setback_rear": 20,
        "typical_setback_side": 10,
        "typical_coverage": 0.30,
        "adu_allowed_default": True,    # MA 40A §3B by-right ADU
        "sewer_default": False,         # Most MA towns are septic
        "calc_crs": "EPSG:26986",
        "adapter": "massgis",
        "state_law": "ma_adu_by_right",
    },
    "NH": {
        "typical_min_lot_sf": 43560,    # 1 acre typical NH zoning
        "typical_setback_front": 40,
        "typical_setback_rear": 30,
        "typical_setback_side": 15,
        "typical_coverage": 0.25,
        "adu_allowed_default": True,
        "sewer_default": False,
        "calc_crs": "EPSG:32618",
        "adapter": "generic",
    },
    "VT": {
        "typical_min_lot_sf": 43560,
        "typical_setback_front": 25,
        "typical_setback_rear": 25,
        "typical_setback_side": 10,
        "typical_coverage": 0.30,
        "adu_allowed_default": True,
        "sewer_default": False,
        "calc_crs": "EPSG:32618",
        "adapter": "generic",
    },
    "CT": {
        "typical_min_lot_sf": 20000,
        "typical_setback_front": 30,
        "typical_setback_rear": 20,
        "typical_setback_side": 10,
        "typical_coverage": 0.30,
        "adu_allowed_default": True,
        "sewer_default": False,
        "calc_crs": "EPSG:26918",
        "adapter": "generic",
    },
    "ME": {
        "typical_min_lot_sf": 43560,
        "typical_setback_front": 30,
        "typical_setback_rear": 20,
        "typical_setback_side": 10,
        "typical_coverage": 0.25,
        "adu_allowed_default": True,
        "sewer_default": False,
        "calc_crs": "EPSG:26919",
        "adapter": "generic",
    },
    "RI": {
        "typical_min_lot_sf": 20000,
        "typical_setback_front": 25,
        "typical_setback_rear": 20,
        "typical_setback_side": 10,
        "typical_coverage": 0.30,
        "adu_allowed_default": True,
        "sewer_default": False,
        "calc_crs": "EPSG:32618",
        "adapter": "generic",
    },
    "NY": {
        "typical_min_lot_sf": 5000,     # Statewide median, but varies widely by region
        "typical_setback_front": 20,    # NYC is tighter than typical suburb
        "typical_setback_rear": 20,
        "typical_setback_side": 5,      # NYC rowhouses can be 3-5ft
        "typical_coverage": 0.45,       # NYC allows higher coverage
        "adu_allowed_default": True,    # NY state ADU law (2024)
        "sewer_default": False,         # Overridden by _detect_sewer() per-municipality
        "calc_crs": "EPSG:32618",
        "adapter": "generic",
        "state_law": "ny_adu_statewide",
    },
    "NJ": {
        "typical_min_lot_sf": 10000,
        "typical_setback_front": 30,
        "typical_setback_rear": 25,
        "typical_setback_side": 8,
        "typical_coverage": 0.35,
        "adu_allowed_default": True,
        "sewer_default": True,
        "calc_crs": "EPSG:32618",
        "adapter": "generic",
    },
    "FL": {
        "typical_min_lot_sf": 7500,
        "typical_setback_front": 25,
        "typical_setback_rear": 20,
        "typical_setback_side": 7,
        "typical_coverage": 0.40,
        "adu_allowed_default": True,
        "sewer_default": True,
        "calc_crs": "EPSG:3086",
        "adapter": "generic",
    },
    "CO": {
        "typical_min_lot_sf": 6000,
        "typical_setback_front": 20,
        "typical_setback_rear": 15,
        "typical_setback_side": 5,
        "typical_coverage": 0.40,
        "adu_allowed_default": True,
        "sewer_default": True,
        "calc_crs": "EPSG:2876",
        "adapter": "generic",
    },
    "WA": {
        "typical_min_lot_sf": 5000,
        "typical_setback_front": 20,
        "typical_setback_rear": 20,
        "typical_setback_side": 5,
        "typical_coverage": 0.35,
        "adu_allowed_default": True,
        "sewer_default": True,
        "calc_crs": "EPSG:2926",
        "adapter": "generic",
    },
    "TX": {
        "typical_min_lot_sf": 5000,
        "typical_setback_front": 25,
        "typical_setback_rear": 20,
        "typical_setback_side": 5,
        "typical_coverage": 0.45,
        "adu_allowed_default": True,
        "sewer_default": True,
        "calc_crs": "EPSG:2277",
        "adapter": "generic",
    },
    "OR": {
        "typical_min_lot_sf": 5000,
        "typical_setback_front": 15,
        "typical_setback_rear": 15,
        "typical_setback_side": 5,
        "typical_coverage": 0.40,
        "adu_allowed_default": True,
        "sewer_default": True,
        "calc_crs": "EPSG:2994",
        "adapter": "generic",
    },
    "NC": {
        "typical_min_lot_sf": 10000,
        "typical_setback_front": 30,
        "typical_setback_rear": 25,
        "typical_setback_side": 8,
        "typical_coverage": 0.35,
        "adu_allowed_default": True,
        "sewer_default": False,
        "calc_crs": "EPSG:3358",
        "adapter": "generic",
    },
    "GA": {
        "typical_min_lot_sf": 7500,
        "typical_setback_front": 25,
        "typical_setback_rear": 20,
        "typical_setback_side": 7,
        "typical_coverage": 0.40,
        "adu_allowed_default": True,
        "sewer_default": True,
        "calc_crs": "EPSG:2240",
        "adapter": "generic",
    },
    "IL": {
        "typical_min_lot_sf": 5000,
        "typical_setback_front": 25,
        "typical_setback_rear": 20,
        "typical_setback_side": 5,
        "typical_coverage": 0.40,
        "adu_allowed_default": True,
        "sewer_default": True,
        "calc_crs": "EPSG:3435",
        "adapter": "generic",
    },
}

# Generic defaults for states not in the table
GENERIC_DEFAULTS = {
    "typical_min_lot_sf": 20000,
    "typical_setback_front": 30,
    "typical_setback_rear": 20,
    "typical_setback_side": 10,
    "typical_coverage": 0.30,
    "adu_allowed_default": True,
    "sewer_default": False,
    "calc_crs": "EPSG:4326",
    "adapter": "generic",
}

# MA state ADU law override
MA_ADU_STATE_LAW = {
    "ma_adu_by_right": {
        "law": "Massachusetts Chapter 40A Section 3B",
        "effective_date": "2024-02-02",
        "description": (
            "All single-family and two-family zoned parcels must permit at least "
            "one ADU by right regardless of local prohibition. "
            "Studio/1BR ADU ≤ 900 sqft or half principal unit size."
        ),
        "applies_to_use_codes": ["single_family", "two_family", "residential"],
        "confidence": 0.9,
        "notes": "Verify applicability with local counsel.",
    }
}

# NY state ADU law override
NY_ADU_STATE_LAW = {
    "ny_adu_statewide": {
        "law": "New York State ADU Legislation (2024)",
        "effective_date": "2024-12-29",
        "description": (
            "New York State requires all municipalities to permit at least one "
            "ADU on single-family and two-family residential lots. ADU may be "
            "attached or detached, up to 1,000 sqft or 50% of principal dwelling "
            "size (whichever is less). Municipalities may impose setback, parking, "
            "and design standards but cannot prohibit ADUs outright."
        ),
        "applies_to_use_codes": ["single_family", "two_family", "residential"],
        "confidence": 0.85,
        "notes": (
            "Statewide preemption. Local municipalities retain control over setbacks, "
            "parking requirements, and design standards. Some municipalities may "
            "have existing ADU ordinances that are more permissive."
        ),
    }
}

# Standard residential zoning district templates by relative scale
# Key: approximate min lot size in acres → district config
DISTRICT_TEMPLATES = {
    "dense_residential": {
        "label": "Dense Residential (auto)",
        "use_allowed": [
            "single_family", "two_family", "multi_family", "mobile_home",
            "condominium", "accessory", "residential", "mixed_use",
        ],
        "max_lot_coverage_pct": 0.55,    # Dense areas often allow 45-65% coverage
        "max_height_ft": 35,
        "adu_allowed": True,
        "adu_max_sqft": 1000,
        "adu_max_bedrooms": 2,
        "adu_parking_required": 1,
        "confidence": 0.45,
    },
    "standard_residential": {
        "label": "Standard Residential (auto)",
        "use_allowed": [
            "single_family", "two_family", "mobile_home",
            "accessory", "residential",
        ],
        "max_lot_coverage_pct": 0.30,
        "max_height_ft": 35,
        "adu_allowed": True,
        "adu_max_sqft": 900,
        "adu_max_bedrooms": 2,
        "adu_parking_required": 1,
        "confidence": 0.40,
    },
    "low_density_residential": {
        "label": "Low Density Residential (auto)",
        "use_allowed": [
            "single_family", "two_family", "mobile_home",
            "accessory", "residential",
        ],
        "max_lot_coverage_pct": 0.20,
        "max_height_ft": 35,
        "adu_allowed": True,
        "adu_max_sqft": 900,
        "adu_max_bedrooms": 2,
        "adu_parking_required": 1,
        "confidence": 0.40,
    },
    "rural_residential": {
        "label": "Rural Residential (auto)",
        "use_allowed": [
            "single_family", "mobile_home", "farmland",
            "accessory", "residential",
        ],
        "max_lot_coverage_pct": 0.15,
        "max_height_ft": 35,
        "adu_allowed": True,
        "adu_notes": "ADU permissibility uncertain in rural zones — verify with local ordinance.",
        "adu_max_sqft": 900,
        "confidence": 0.35,
    },
}


# ---------------------------------------------------------------------------
# Zoning code discovery
# ---------------------------------------------------------------------------

def discover_zoning_codes(gdf, state: str = None) -> list[str]:
    """
    Extract unique non-null zoning codes from a GeoDataFrame.

    Uses the state's field_map to find the correct zoning column,
    then falls back to common column name guessing.
    """
    from app.agents.state_gis_registry import get_state_config  # avoid circular
    zoning_col = None

    # First: use state registry field_map if available
    if state:
        state_cfg = get_state_config(state)
        if state_cfg:
            mapped_col = state_cfg.get("field_map", {}).get("zoning_code")
            if mapped_col and mapped_col in gdf.columns:
                zoning_col = mapped_col

    # Fallback: try common zoning column names
    if not zoning_col:
        for col in ["ZONING", "ZONE_CODE", "ZONE_", "DISTRICT", "ZONE",
                     "ZONING_CD", "CAT", "SLU", "LAND_USE", "PROPTYPE"]:
            if col in gdf.columns:
                zoning_col = col
                break

    if not zoning_col:
        return []

    codes = (
        gdf[zoning_col]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )
    # Filter out empty/null strings and generic non-useful values
    skip_values = {"NONE", "NULL", "NAN", "0", "", "PARCEL", "UNKNOWN", "N/A"}
    codes = [c for c in codes if c and c.upper() not in skip_values]
    codes.sort()
    return codes


def classify_zoning_code(code: str) -> str:
    """
    Classify a raw zoning code into a residential district template.
    Returns template key from DISTRICT_TEMPLATES.

    Handles:
      - Standard zoning codes (R-1, VR, etc.)
      - VT CAT codes (R1, R2, CA, M, MHL, etc.)
      - NH SLU codes (11=SF, 12=MF, 14=MH, 18=Seasonal, etc.)
    """
    code_upper = code.upper().strip()

    # ── NH State Land Use (SLU) numeric codes ──────────────────────────
    # These are 2-digit codes, sometimes with suffix (e.g. "11-70")
    slu_base = code_upper.split("-")[0] if "-" in code_upper else code_upper
    NH_SLU_MAP = {
        "11": "standard_residential",    # Single family
        "12": "dense_residential",       # Multi-family
        "13": "rural_residential",       # Farm/forest residential
        "14": "standard_residential",    # Manufactured housing
        "17": "low_density_residential", # Other improved residential
        "18": "rural_residential",       # Seasonal / camp
        "19": "standard_residential",    # Other residential
        "21": "non_residential",         # Grocery/convenience
        "22": "non_residential",         # Retail
        "23": "non_residential",         # Eating/drinking
        "24": "non_residential",         # Service station
        "25": "non_residential",         # Vehicle sales
        "26": "non_residential",         # Other retail
        "27": "dense_residential",       # Multi-use (commercial+residential)
        "28": "non_residential",         # Recreation commercial
        "29": "non_residential",         # Other commercial
        "33": "non_residential",         # Office
        "34": "non_residential",         # Office condo
        "37": "non_residential",         # Health care
        "38": "non_residential",         # Government
        "39": "non_residential",         # Other service
        "40": "non_residential",         # Manufacturing
        "41": "non_residential",         # Manufacturing
        "42": "non_residential",         # Manufacturing
        "43": "non_residential",         # Warehouse
        "44": "non_residential",         # Warehouse
        "50": "non_residential",         # Institutional
        "51": "non_residential",         # Religious
        "52": "non_residential",         # Educational
        "53": "non_residential",         # Government
        "57": "non_residential",         # Church/exempt
        "60": "non_residential",         # Utility
        "70": "rural_residential",       # Undeveloped land
        "71": "rural_residential",       # Undeveloped land
        "72": "rural_residential",       # Forest
        "73": "rural_residential",       # Farmland
        "74": "non_residential",         # Wetland
        "75": "non_residential",         # Flood zone
        "80": "non_residential",         # Recreation/conservation
        "90": "non_residential",         # Other
    }
    # ── NYC Tax Class codes (1-2 digit) ─────────────────────────────────
    # NYC uses a different system: tax class, not property classification.
    # MUST be checked BEFORE NH SLU codes since both are 2-digit numeric.
    NYC_TAX_CLASS_MAP = {
        "01": "dense_residential",       # 1-3 family residential
        "1":  "dense_residential",       # same without leading zero
        "02": "dense_residential",       # 4+ family residential / co-op
        "2":  "dense_residential",
        "03": "non_residential",         # Utility
        "3":  "non_residential",
        "04": "non_residential",         # Commercial/industrial
        "4":  "non_residential",
        "05": "dense_residential",       # Condos (various, often residential)
        "5":  "dense_residential",
        "06": "dense_residential",       # Residential condos
        "6":  "dense_residential",
        "07": "non_residential",         # Commercial condos
        "7":  "non_residential",
        "08": "dense_residential",       # Mixed use
        "8":  "dense_residential",
        "09": "non_residential",         # Utility condos
        "9":  "non_residential",
        "10": "low_density_residential", # Vacant land
        "11": "dense_residential",       # Residential condos (alternate)
    }
    if code_upper in NYC_TAX_CLASS_MAP:
        return NYC_TAX_CLASS_MAP[code_upper]
    # Zero-padded variant
    if code_upper.isdigit() and len(code_upper) <= 2:
        padded = code_upper.zfill(2)
        if padded in NYC_TAX_CLASS_MAP:
            return NYC_TAX_CLASS_MAP[padded]

    # ── NH State Land Use (SLU) numeric codes ──────────────────────────
    # After NYC check since both use 2-digit codes
    if slu_base.isdigit() and len(slu_base) == 2:
        return NH_SLU_MAP.get(slu_base, "standard_residential")

    # ── NY Property Class codes (3-digit) ──────────────────────────────
    NY_PROP_MAP = {
        "210": "standard_residential",   # 1-family
        "211": "standard_residential",   # 1-family
        "215": "standard_residential",   # 1-family w/ accessory
        "220": "dense_residential",      # 2-family
        "230": "dense_residential",      # 3-family
        "240": "rural_residential",      # Rural/community res
        "250": "low_density_residential",# Estate
        "260": "rural_residential",      # Seasonal
        "270": "standard_residential",   # Mobile home
        "280": "dense_residential",      # Multiple residences
        "281": "standard_residential",   # Multiple (1-family)
        "283": "dense_residential",      # Res w/ commercial
        "311": "low_density_residential",# Vacant res land
        "312": "low_density_residential",# Vacant res <10ac
        "314": "rural_residential",      # Vacant rural res
    }
    if slu_base.isdigit() and len(slu_base) == 3:
        if slu_base in NY_PROP_MAP:
            return NY_PROP_MAP[slu_base]
        first = slu_base[0]
        if first in ("4", "5", "6", "7", "8", "9"):
            return "non_residential"
        if first == "3":
            return "rural_residential"  # vacant/farm land

    # ── NJ Property Class codes ────────────────────────────────────────
    NJ_PROP_MAP = {
        "1":  "low_density_residential", # Vacant land
        "2":  "standard_residential",    # Residential
        "3A": "rural_residential",       # Farm regular
        "3B": "rural_residential",       # Farm qualified
        "4A": "non_residential",         # Commercial
        "4B": "non_residential",         # Industrial
        "4C": "dense_residential",       # Apartments 5+
    }
    if code_upper in NJ_PROP_MAP:
        return NJ_PROP_MAP[code_upper]

    # ── VT CAT codes ──────────────────────────────────────────────────
    VT_CAT_MAP = {
        "R1":  "standard_residential",   # Residential 1 (primary)
        "R2":  "dense_residential",      # Residential 2 (secondary/rental)
        "CA":  "dense_residential",      # Commercial apartments
        "M":   "dense_residential",      # Multi-family
        "MHL": "standard_residential",   # Mobile home land
        "S1":  "rural_residential",      # Seasonal 1
        "S2":  "rural_residential",      # Seasonal 2
        "C":   "non_residential",        # Commercial
        "I":   "non_residential",        # Industrial
        "F":   "rural_residential",      # Farm
        "UE":  "non_residential",        # Utility electric
        "UO":  "non_residential",        # Utility other
        "W":   "non_residential",        # Woodland
        "O":   "non_residential",        # Other
    }
    if code_upper in VT_CAT_MAP:
        return VT_CAT_MAP[code_upper]

    # ── Standard zoning code classification ────────────────────────────

    # Commercial/Industrial/Other — skip these for Plinth purposes
    non_residential = ["COM", "IND", "COMM", "BUS", "INDUSTRIAL", "COMMERCIAL",
                       "MIXED", "PUD", "OS", "OPEN", "WATER", "AGR", "FARM",
                       "RECR", "EXEMPT", "PUBLIC", "CIVIC"]
    for nr in non_residential:
        if nr in code_upper:
            return "non_residential"

    # Single-letter codes: B (business), C (commercial), I (industrial), P (public)
    if code_upper in ("B", "C", "I", "P"):
        return "non_residential"

    # Dense/Village residential
    dense_hints = ["VR", "VILLAGE", "VILL", "URB", "URBAN", "R-1", "R1",
                   "RES-1", "RS-1", "RA", "RB"]
    for h in dense_hints:
        if h in code_upper or code_upper.startswith(h):
            return "dense_residential"

    # Rural/Large lot
    rural_hints = ["R-4", "R-8", "R4", "R8", "RR", "RURAL", "AG", "AGRI",
                   "R-3A", "R-5", "R5", "R6", "R7", "R8", "R10", "RA-"]
    for h in rural_hints:
        if h in code_upper or code_upper.startswith(h):
            return "rural_residential"

    # Low density
    low_hints = ["R-2", "R-3", "R2", "R3", "RES-2", "RES-3", "RS-2", "RS-3"]
    for h in low_hints:
        if h in code_upper or code_upper.startswith(h):
            return "low_density_residential"

    # Default: standard residential if it contains "R" at all
    if "R" in code_upper and any(c.isdigit() for c in code_upper):
        return "standard_residential"

    # Final fallback
    return "standard_residential"


# ---------------------------------------------------------------------------
# Config generator
# ---------------------------------------------------------------------------

def generate_municipality_config(
    municipality_id: str,
    municipality_name: str,
    state: str,
    county: str,
    zoning_codes: list[str],
    median_lot_sqft: Optional[float] = None,
    sewer_override: Optional[bool] = None,
    district_lot_stats: Optional[dict] = None,
) -> dict:
    """
    Generate a baseline municipality config dict.

    Args:
        municipality_id: e.g. "vt_burlington"
        municipality_name: e.g. "Burlington"
        state: e.g. "VT"
        county: e.g. "Chittenden"
        zoning_codes: raw zoning codes discovered from parcel data
        median_lot_sqft: median lot size from parcel data (for scaling defaults)
        sewer_override: force sewer_service True/False (None = use state default)
        district_lot_stats: per-district lot stats {code: {median, p25, p75, count}}

    Returns:
        Config dict matching the ma_acton.json structure
    """
    defaults = STATE_DEFAULTS.get(state, GENERIC_DEFAULTS)
    district_lot_stats = district_lot_stats or {}

    # Sewer detection: explicit override > regional detection > state default
    if sewer_override is not None:
        sewer = sewer_override
    else:
        sewer = _detect_sewer(municipality_name, state, county) or defaults["sewer_default"]

    # Scale typical min lot size using median if available (global fallback)
    if median_lot_sqft and median_lot_sqft > 5000:
        scaled_min_lot = int(median_lot_sqft * 1.5)
    else:
        scaled_min_lot = defaults["typical_min_lot_sf"]

    # Build zoning districts from discovered codes
    districts = {}
    zoning_code_map = {}

    if not zoning_codes:
        zoning_codes = ["RES"]

    for code in zoning_codes:
        template_key = classify_zoning_code(code)

        if template_key == "non_residential":
            zoning_code_map[code] = None
            continue

        template = DISTRICT_TEMPLATES[template_key].copy()

        # Use per-district stats if available, otherwise fall back to global heuristic
        lot_scale = {
            "dense_residential": 0.4,
            "standard_residential": 0.7,
            "low_density_residential": 1.0,
            "rural_residential": 2.0,
        }
        stats = district_lot_stats.get(code)
        if stats and stats.get("median") and stats["median"] > 2000 and stats.get("count", 0) >= 5:
            # Use real per-district median: p25 as min lot (smaller lots exist),
            # clamped to at least 50% of the median
            district_median = stats["median"]
            p25 = stats.get("p25", district_median * 0.7)
            min_lot = max(int(p25), int(district_median * 0.5))
            template["min_lot_area_sqft"] = min_lot
            template["_lot_stats"] = {
                "median_sqft": int(district_median),
                "p25_sqft": int(p25),
                "p75_sqft": int(stats.get("p75", district_median * 1.3)),
                "parcel_count": stats["count"],
            }
            # Higher confidence when we have real per-district lot stats
            # 5 parcels = 0.45, 20 = 0.55, 50 = 0.65, 100+ = 0.70
            template["confidence"] = min(0.70, 0.40 + stats["count"] / 200)
        else:
            template["min_lot_area_sqft"] = int(scaled_min_lot * lot_scale.get(template_key, 1.0))

        template["min_frontage_ft"] = max(60, int(defaults["typical_setback_front"] * 2))
        template["setbacks"] = {
            "front_ft": defaults["typical_setback_front"],
            "rear_ft": defaults["typical_setback_rear"],
            "side_ft": defaults["typical_setback_side"],
        }
        template["max_lot_coverage_pct"] = defaults["typical_coverage"]
        template["far"] = None
        template["adu_allowed"] = defaults["adu_allowed_default"]

        if "adu_notes" not in template:
            template["adu_notes"] = (
                f"Auto-generated config for {municipality_name}. "
                "Verify ADU rules against local zoning ordinance."
            )

        # Add citations note
        template["citations"] = [f"Auto-generated — verify against {municipality_name} zoning ordinance"]

        district_key = code.replace(" ", "_").replace("/", "_").replace("-", "_")
        districts[district_key] = template
        # Map raw code to district key (identity map for now)
        zoning_code_map[code] = district_key

    # NY prop class + NYC tax class code backfill: ensure all residential
    # codes map to a residential district even if they weren't in the sample.
    if state == "NY":
        NY_RESIDENTIAL_CODES = [
            # 3-digit property class (suburban/upstate)
            "210", "211", "212", "213", "214", "215", "216", "217", "218", "219",
            "220", "221", "222", "223", "224", "225", "226", "227", "228", "229",
            "230", "240", "241", "242", "250", "260", "270", "280", "281", "283",
            # 2-digit NYC tax class (residential)
            "01", "1", "02", "2", "05", "5", "06", "6", "08", "8", "11",
        ]
        NY_NON_RESIDENTIAL_CODES = [
            "300", "311", "312", "314", "322", "330", "340",
            "400", "411", "421", "432", "449", "464", "480",
            "500", "600", "620", "651", "695",
            "710", "720", "800", "900",
            # NYC tax class (non-residential)
            "03", "3", "04", "4", "07", "7", "09", "9", "10",
        ]
        # Find a residential district key to map to
        res_district_key = None
        for dk, dv in districts.items():
            if any(u in (dv.get("use_allowed") or []) for u in ["single_family", "residential"]):
                res_district_key = dk
                break
        if not res_district_key and districts:
            res_district_key = next(iter(districts))
        if res_district_key:
            for code in NY_RESIDENTIAL_CODES:
                if code not in zoning_code_map:
                    zoning_code_map[code] = res_district_key
            for code in NY_NON_RESIDENTIAL_CODES:
                if code not in zoning_code_map:
                    zoning_code_map[code] = None

    # State law overrides
    state_law_overrides = {}
    if state == "MA":
        state_law_overrides = MA_ADU_STATE_LAW
    elif state == "NY":
        state_law_overrides = NY_ADU_STATE_LAW

    config = {
        "municipality_id": municipality_id,
        "municipality_name": municipality_name,
        "county": county,
        "state": state,
        "config_version": 1,
        "config_notes": (
            f"AUTO-GENERATED config for {municipality_name}, {state}. "
            f"Generated from parcel data with {len(zoning_codes)} discovered zoning codes. "
            "All zoning rules are generic defaults — requires verification against "
            "the actual local zoning ordinance before using for real outreach decisions."
        ),
        "auto_generated": True,
        "auto_generated_confidence": "LOW",
        "crs": "EPSG:4326",
        "calc_crs": defaults.get("calc_crs", "EPSG:4326"),
        "adapter": defaults.get("adapter", "generic"),
        "zoning_code_map": zoning_code_map,
        "state_law_overrides": state_law_overrides,
        "data_sources": {
            "parcel_data": {
                "url": "ArcGIS REST API (auto-fetched)",
                "format": "arcgis_rest",
                "confidence": 0.8,
                "notes": "Auto-fetched from state GIS portal via Plinth Phase 5 scanner.",
            },
            "zoning_data": {
                "url": None,
                "format": "unknown",
                "confidence": 0.2,
                "notes": "Zoning rules are auto-generated defaults. Verify with local ordinance.",
            },
        },
        "sewer_service": sewer,
        "sewer_service_notes": (
            f"Assumed {'municipal sewer' if sewer else 'private septic (no sewer)'} "
            f"based on {state} state defaults. Verify with local public works."
        ),
        "sewer_service_confidence": 0.4,
        "zoning_districts": districts,
        "septic_assumptions": {
            "min_lot_area_for_new_system_sqft": 40000,
            "bedroom_load_factor": "standard",
            "title_5_perc_test_required": (state == "MA"),
            "notes": (
                "Auto-generated septic assumptions based on state defaults. "
                "Verify with local health regulations."
            ),
            "confidence": 0.4,
        },
        "parking_assumptions": {
            "default_spaces_required_per_unit": 1,
            "notes": "One off-street space assumed. Verify with local ordinance.",
            "confidence": 0.5,
        },
        "overlays": [
            {
                "overlay_type": "flood_zone",
                "label": "FEMA Flood Zone",
                "constraint_level": "hard_block",
                "notes": "Standard FEMA flood zone constraint. Overlay GIS data not yet loaded.",
            },
            {
                "overlay_type": "wetlands_buffer",
                "label": "Wetlands Buffer",
                "constraint_level": "hard_block",
                "notes": "State wetlands regulations apply. Buffer distances vary by state.",
            },
        ],
    }

    return config


def save_municipality_config(config: dict, configs_dir: str) -> str:
    """Save a municipality config to the configs/municipalities/ directory."""
    out_dir = Path(configs_dir) / "municipalities"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{config['municipality_id']}.json"
    with open(out_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"  Saved config: {out_path}")
    return str(out_path)


def _detect_sewer(municipality_name: str, state: str, county: str) -> bool:
    """
    Detect whether a municipality likely has municipal sewer service.
    Uses county and municipality name heuristics.
    """
    name_lower = municipality_name.lower()
    county_lower = county.lower() if county else ""

    # Generic: anything called "city" likely has sewer
    if "city" in name_lower:
        return True

    if state == "NY":
        # NYC boroughs
        NYC_BOROUGHS = {"manhattan", "brooklyn", "queens", "bronx", "staten island",
                        "new york", "kings", "richmond"}
        if name_lower in NYC_BOROUGHS or county_lower in NYC_BOROUGHS:
            return True

        # Long Island (Nassau + Suffolk west) — mostly sewered
        if county_lower in ("nassau", "nassau county"):
            return True
        if county_lower in ("suffolk", "suffolk county"):
            # Western Suffolk (Babylon, Islip, Huntington, Smithtown) has sewer
            # Eastern Suffolk (Riverhead, Southampton, East Hampton, Shelter Island) mostly septic
            WESTERN_SUFFOLK = {"babylon", "islip", "huntington", "smithtown", "brookhaven"}
            if name_lower in WESTERN_SUFFOLK:
                return True
            return False  # assume septic for eastern Suffolk

        # Westchester — most municipalities have sewer
        if county_lower in ("westchester", "westchester county"):
            return True

        # Rockland, Putnam — mixed but mostly sewered in villages/towns
        if county_lower in ("rockland", "rockland county"):
            return True

        # Major NY cities
        NY_CITIES_WITH_SEWER = {
            "buffalo", "rochester", "syracuse", "albany", "yonkers",
            "new rochelle", "mount vernon", "white plains", "troy",
            "schenectady", "utica", "binghamton", "poughkeepsie",
            "newburgh", "kingston", "ithaca", "saratoga springs",
        }
        if name_lower in NY_CITIES_WITH_SEWER:
            return True

        # Default: upstate/rural NY = septic
        return False

    elif state == "NJ":
        # Most NJ municipalities have sewer
        return True

    elif state == "MA":
        # MA default is septic (set in STATE_DEFAULTS)
        return False

    return False


def generate_and_save_config(
    municipality_id: str,
    municipality_name: str,
    state: str,
    county: str,
    configs_dir: str,
    gdf=None,
    sewer_override: Optional[bool] = None,
) -> dict:
    """
    Generate config from parcel GDF (if available) and save to disk.

    If an existing config already exists, returns it unchanged (don't overwrite
    analyst-refined configs with auto-generated ones).
    """
    out_path = Path(configs_dir) / "municipalities" / f"{municipality_id}.json"

    if out_path.exists():
        print(f"  Config already exists: {out_path} — using existing config.")
        with open(out_path) as f:
            return json.load(f)

    # Discover zoning codes and lot sizes from parcel data
    zoning_codes = []
    median_lot_sqft = None
    from app.agents.state_gis_registry import get_state_config
    state_cfg = get_state_config(state)

    if gdf is not None and len(gdf) > 0:
        zoning_codes = discover_zoning_codes(gdf, state=state)
        print(f"  Discovered zoning codes: {zoning_codes[:20]}")

        # Try to get lot size field
        if state_cfg:
            lot_field = state_cfg.get("field_map", {}).get("lot_size")
            unit = state_cfg.get("lot_size_unit", "sqft")
            if lot_field and lot_field in gdf.columns:
                import pandas as pd
                lot_vals = pd.to_numeric(gdf[lot_field], errors="coerce").dropna()
                if len(lot_vals) > 0:
                    median_raw = float(lot_vals.median())
                    if unit == "acres":
                        median_lot_sqft = median_raw * 43560
                    else:
                        median_lot_sqft = median_raw
                    print(f"  Median lot size: {median_lot_sqft:.0f} sqft ({median_raw:.2f} {unit})")

    if not zoning_codes:
        print("  No zoning codes discovered — using generic residential district.")

    # Compute per-district lot size statistics
    district_lot_stats = {}
    if gdf is not None and len(gdf) > 0 and state_cfg:
        lot_field = state_cfg.get("field_map", {}).get("lot_size")
        unit = state_cfg.get("lot_size_unit", "sqft")
        zoning_field = state_cfg.get("field_map", {}).get("zoning_code")
        if lot_field and lot_field in gdf.columns and zoning_field and zoning_field in gdf.columns:
            import pandas as pd
            import numpy as np
            gdf_copy = gdf[[zoning_field, lot_field]].copy()
            gdf_copy["_lot_sqft"] = pd.to_numeric(gdf_copy[lot_field], errors="coerce")
            if unit == "acres":
                gdf_copy["_lot_sqft"] = gdf_copy["_lot_sqft"] * 43560
            gdf_copy["_zone"] = gdf_copy[zoning_field].astype(str).str.strip()

            for code, group in gdf_copy.groupby("_zone"):
                vals = group["_lot_sqft"].dropna()
                if len(vals) >= 3:
                    district_lot_stats[code] = {
                        "median": float(vals.median()),
                        "p25": float(np.percentile(vals, 25)),
                        "p75": float(np.percentile(vals, 75)),
                        "count": len(vals),
                    }

            if district_lot_stats:
                print(f"  Per-district lot stats computed for {len(district_lot_stats)} districts")

    # Auto-detect sewer for urban/suburban areas
    if sewer_override is None:
        sewer_override = _detect_sewer(municipality_name, state, county)
        if sewer_override:
            print(f"  Sewer: assumed municipal sewer (regional detection)")

    config = generate_municipality_config(
        municipality_id=municipality_id,
        municipality_name=municipality_name,
        state=state,
        county=county,
        zoning_codes=zoning_codes,
        median_lot_sqft=median_lot_sqft,
        sewer_override=sewer_override,
        district_lot_stats=district_lot_stats,
    )

    save_municipality_config(config, configs_dir)
    return config
