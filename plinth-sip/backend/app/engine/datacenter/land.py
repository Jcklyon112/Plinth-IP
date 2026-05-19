"""Land suitability quick flags: acreage tier and zoning compatibility."""
from __future__ import annotations

from typing import Optional

# Tier thresholds match the spec.
ACREAGE_EDGE_MAX = 5.0
ACREAGE_COLO_MAX = 25.0
ACREAGE_HYPERSCALE_MIN = 25.0
ACREAGE_CAMPUS_MIN = 100.0


def sqft_to_acres(sqft: Optional[float]) -> Optional[float]:
    if sqft is None:
        return None
    return sqft / 43560.0


def acreage_tier(acres: Optional[float]) -> Optional[str]:
    """Single tier label per the spec.

    Edge (<5 ac), Colo (5-25 ac), Hyperscale-capable (25+ ac),
    Campus-capable (100+ ac).
    """
    if acres is None:
        return None
    if acres >= ACREAGE_CAMPUS_MIN:
        return "campus"
    if acres >= ACREAGE_HYPERSCALE_MIN:
        return "hyperscale"
    if acres >= ACREAGE_EDGE_MAX:
        return "colo"
    return "edge"


def tier_fit(acres: Optional[float]) -> list[str]:
    """List of all tiers a parcel of `acres` can support.

    Returns an empty list when acreage is unknown so the analyzer can
    render a clear "unknown" rather than implying inability.
    """
    if acres is None:
        return []
    fits: list[str] = []
    if acres >= 1.0:                    # below 1 ac is sub-edge; analyzer F-gates it
        fits.append("edge")
    if acres >= ACREAGE_EDGE_MAX:
        fits.append("colo")
    if acres >= ACREAGE_HYPERSCALE_MIN:
        fits.append("hyperscale")
    if acres >= ACREAGE_CAMPUS_MIN:
        fits.append("campus")
    return fits


# Zoning compatibility tags. Inputs are the parcel's normalized
# `land_use_type` plus the raw zoning code. The spec asked for one of:
# industrial / heavy commercial / agricultural / residential / mixed.
INDUSTRIAL_TOKENS = ("INDUSTRIAL", "INDUST", "MFG", "MANUF", "WAREHOUSE", "I-1", "I-2", "I-3", "M-1", "M-2", "M-3", "M1", "M2", "M3")
HEAVY_COMMERCIAL_TOKENS = ("HEAVY COMMERCIAL", "C-3", "C3", "GENERAL COMMERCIAL", "BUSINESS PARK", "BP")
COMMERCIAL_TOKENS = ("COMMERCIAL", "COMM", "C-1", "C-2", "C-1", "C1", "C2", "BUSINESS")
AGRICULTURAL_TOKENS = ("AGRIC", "AGRI", "FARM", "RURAL", "A-1", "AG", "A1")
RESIDENTIAL_TOKENS = ("RESIDENTIAL", "RES", "R-1", "R-2", "R-3", "R-4", "R1", "R2", "R3", "R4")
MIXED_TOKENS = ("MIXED", "MX", "MU", "MUD")


def zoning_compatibility(zoning_code: Optional[str], land_use_type: Optional[str] = None) -> str:
    """Return one of: industrial | heavy_commercial | commercial |
    agricultural | residential | mixed | unknown.

    The string is informational; scoring.py applies the data-center
    suitability weighting.
    """
    candidates = [s for s in (zoning_code, land_use_type) if s]
    if not candidates:
        return "unknown"
    blob = " | ".join(c.upper() for c in candidates)

    if any(tok in blob for tok in INDUSTRIAL_TOKENS):
        return "industrial"
    if any(tok in blob for tok in HEAVY_COMMERCIAL_TOKENS):
        return "heavy_commercial"
    if any(tok in blob for tok in MIXED_TOKENS):
        return "mixed"
    if any(tok in blob for tok in COMMERCIAL_TOKENS):
        return "commercial"
    if any(tok in blob for tok in AGRICULTURAL_TOKENS):
        return "agricultural"
    if any(tok in blob for tok in RESIDENTIAL_TOKENS):
        return "residential"
    return "unknown"
