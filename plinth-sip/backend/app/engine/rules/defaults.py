"""
Aggregate fallback defaults for district config values.

When a municipality's config doesn't define a particular dimensional, use, or
ADU value for a district, we substitute typical-residential aggregates so the
engine can still produce a meaningful score instead of bailing with N/A.

Every rule that consumes a defaulted value MUST mark its explanation with
ASSUMED_MARK ("*") and apply ASSUMED_CONFIDENCE_FACTOR to its confidence so
that analysts know the answer rests on an assumption, not on local code.
"""

from __future__ import annotations

# Visible marker placed at the end of any rule explanation that consumed an
# assumed default. Surface this in every UI/PDF that renders explanations.
ASSUMED_MARK = "*"

ASSUMED_FOOTNOTE = (
    "* Value assumed from typical residential aggregate — local ordinance not "
    "available. Verify against the municipality's zoning bylaw before relying "
    "on this result."
)

# Multiplier applied to a rule's confidence whenever it relied on an assumed
# default. 0.5 — half-confidence — is intentionally aggressive: we want
# analysts to see immediately that this rule is on shaky ground.
ASSUMED_CONFIDENCE_FACTOR = 0.5

# Typical low/medium-density residential aggregates. These are deliberately
# conservative for siting (smaller min lot than rural, larger setbacks than
# urban) and optimistic for use/ADU (most jurisdictions allow at least
# single-family and many states have moved to ADU-by-right).
DEFAULT_DISTRICT_CONFIG: dict = {
    "min_lot_area_sqft": 15000,        # ~1/3 acre — typical suburban residential
    "max_lot_coverage_pct": 0.30,      # 30% — common cap in suburban res zones
    "adu_max_sqft": 900,               # MA AHA cap; aligns with most state ADU statutes
    "adu_allowed": True,               # Optimistic: assume residential allows ADU
    "adu_notes": (
        "Assumed ADU allowed per typical residential zoning conventions. "
        "Verify with local ordinance."
    ),
    "use_allowed": ["single_family", "two_family", "residential", "accessory"],
    "setbacks": {
        "front_ft": 30,
        "rear_ft": 30,
        "side_ft": 15,
    },
    # Confidence isn't itself defaulted onto the config; rules apply
    # ASSUMED_CONFIDENCE_FACTOR directly when they used a defaulted key.
}

# Keys at the top level of the district config that may be defaulted.
# `setbacks` is a nested dict — handled separately so per-direction
# defaults (front/rear/side) flow in even when only some are defined.
_TOP_LEVEL_DEFAULT_KEYS = {
    "min_lot_area_sqft",
    "max_lot_coverage_pct",
    "adu_max_sqft",
    "adu_allowed",
    "use_allowed",
}


def apply_district_defaults(district_config: dict | None) -> dict:
    """
    Return a copy of district_config with missing keys filled from
    DEFAULT_DISTRICT_CONFIG. The set of keys that were filled is recorded on
    the returned dict under `_assumed_defaults` (a set of strings).

    Recognized assumed-default keys:
      - "min_lot_area_sqft"
      - "max_lot_coverage_pct"
      - "adu_max_sqft"
      - "adu_allowed"
      - "use_allowed"
      - "setbacks"            (any side missing → setback inherits from default)
      - "adu_notes"            (only set when adu_allowed itself was defaulted)

    The original district_config is not mutated.
    """
    cfg = dict(district_config or {})
    assumed: set[str] = set()

    for key in _TOP_LEVEL_DEFAULT_KEYS:
        val = cfg.get(key)
        is_missing = val is None or (isinstance(val, list) and len(val) == 0)
        if is_missing:
            cfg[key] = DEFAULT_DISTRICT_CONFIG[key]
            assumed.add(key)

    # adu_notes piggybacks on adu_allowed: only inject the explanatory note
    # when the allowance itself was assumed. If the municipality already has
    # adu_allowed defined, leave their notes (or lack thereof) alone.
    if "adu_allowed" in assumed and not cfg.get("adu_notes"):
        cfg["adu_notes"] = DEFAULT_DISTRICT_CONFIG["adu_notes"]

    # Setbacks: nested dict — fill missing front/rear/side from defaults.
    setbacks_in = cfg.get("setbacks") or {}
    setbacks_out = dict(setbacks_in)
    default_setbacks = DEFAULT_DISTRICT_CONFIG["setbacks"]
    setback_assumed = False
    for side_key, default_val in default_setbacks.items():
        v = setbacks_out.get(side_key)
        if v is None:
            setbacks_out[side_key] = default_val
            setback_assumed = True
    cfg["setbacks"] = setbacks_out
    if setback_assumed:
        assumed.add("setbacks")

    cfg["_assumed_defaults"] = assumed
    return cfg


def was_assumed(district_config: dict, key: str) -> bool:
    """True if `key` was filled from defaults during apply_district_defaults."""
    return key in (district_config.get("_assumed_defaults") or set())


def mark(explanation: str) -> str:
    """Append the assumed-default marker to a rule explanation."""
    if not explanation:
        return ASSUMED_MARK
    return f"{explanation.rstrip()} {ASSUMED_MARK}"


def adjust_confidence(confidence: float | None) -> float:
    """Apply the assumed-default confidence penalty to a rule confidence."""
    if confidence is None:
        return 0.0
    return float(confidence) * ASSUMED_CONFIDENCE_FACTOR
