"""ISO/RTO assignment helpers.

Two paths:

* **Pure**: `iso_for_ba_code(ba_code, metadata)` maps a HIFLD/EIA
  Balancing Authority code (e.g. "PJM", "ERCO", "TVA") to an ISO bucket
  using `iso_metadata.json`. Used at load time by the BA loader and
  unit-tested directly. No DB.
* **Spatial**: `iso_for_point(session, lon, lat)` runs a PostGIS
  ST_Contains against `grid_balancing_authorities` and returns the
  pre-tagged `iso_rto`. Used by the analyzer (Phase 2).

The iso_metadata.json file is hand-edited; the analyzer surfaces its
`current_posture`, `queue_dashboard_url`, and `typical_queue_timeline`
verbatim in the report.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional, TypedDict

log = logging.getLogger(__name__)


# Path to iso_metadata.json. Resolved relative to the repo root by
# walking up from this file (backend/app/engine/datacenter/iso.py ->
# repo/data/grid/iso_metadata.json).
def _default_metadata_path() -> Path:
    return Path(__file__).resolve().parents[4] / "data" / "grid" / "iso_metadata.json"


class IsoEntry(TypedDict, total=False):
    name: str               # canonical ISO key, e.g. "PJM"
    full_name: str
    queue_dashboard_url: Optional[str]
    typical_queue_timeline: str
    current_posture: str
    states: list[str]
    ba_codes: list[str]


@lru_cache(maxsize=4)
def load_iso_metadata(path: Optional[str] = None) -> dict:
    """Read iso_metadata.json. Cached; pass an explicit path to force a
    distinct cache slot in tests."""
    p = Path(path) if path else _default_metadata_path()
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def iso_for_ba_code(
    ba_code: str,
    metadata: Optional[dict] = None,
    *,
    ba_name: Optional[str] = None,
) -> str:
    """Pure mapping from a Balancing Authority code to an ISO bucket.

    Returns one of: PJM | MISO | ERCOT | CAISO | NYISO | ISO-NE | SPP | NON-ISO.

    Some sources (notably HIFLD Control_Areas) carry FERC respondent IDs in
    the code field rather than EIA BA abbreviations, so an optional
    `ba_name` triggers a third-tier substring match against per-ISO
    `ba_name_keywords` in iso_metadata.json. Unknown inputs get logged
    once and bucketed as NON-ISO; this is the correct default for
    vertically integrated utilities outside the seven ISOs/RTOs.
    """
    if metadata is None:
        metadata = load_iso_metadata()
    code = (ba_code or "").strip().upper()

    isos = metadata.get("isos", {})
    # 1) explicit per-ISO ba_codes list
    if code:
        for iso_key, entry in isos.items():
            for c in entry.get("ba_codes", []) or []:
                if c.upper() == code:
                    return iso_key
        # 2) overrides for known non-ISO BAs
        overrides = metadata.get("ba_code_overrides", {}) or {}
        for c, iso_key in overrides.items():
            if c.startswith("_"):
                continue
            if c.upper() == code:
                return iso_key
    # 3) name-based fallback (HIFLD Control_Areas has full company names,
    #    no EIA abbreviation). First match wins; keywords are uppercased
    #    substrings, so be specific in the JSON config.
    if ba_name:
        name = ba_name.strip().upper()
        for iso_key, entry in isos.items():
            for kw in entry.get("ba_name_keywords", []) or []:
                if kw and kw.upper() in name:
                    return iso_key
    # 4) unknown -> NON-ISO with a single warning per (code, name) pair
    _warn_unknown_ba(code or (ba_name or ""))
    return "NON-ISO"


@lru_cache(maxsize=512)
def _warn_unknown_ba(code: str) -> None:
    log.warning("Unknown balancing authority code %r; bucketing as NON-ISO", code)


def iso_metadata_entry(iso_key: str, metadata: Optional[dict] = None) -> dict:
    """Return the full metadata block for an ISO key, with a 'name' field
    added. Used by the analyzer to surface posture / queue URL verbatim.
    """
    if metadata is None:
        metadata = load_iso_metadata()
    entry = (metadata.get("isos", {}) or {}).get(iso_key) or {}
    out = dict(entry)
    out["name"] = iso_key
    return out


# --- DB-backed lookup (used in Phase 2) -------------------------------

def iso_for_point(session, lon: float, lat: float) -> Optional[str]:
    """Return the iso_rto for the BA polygon containing (lon, lat).

    None if the point falls outside every loaded BA polygon (e.g. the
    BA layer hasn't been loaded yet, or the parcel is offshore).
    """
    from sqlalchemy import text
    sql = text(
        """
        SELECT iso_rto
        FROM grid_balancing_authorities
        WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
        ORDER BY ST_Area(geom) ASC  -- prefer smaller (more specific) polygon on overlap
        LIMIT 1
        """
    )
    row = session.execute(sql, {"lon": lon, "lat": lat}).first()
    return row[0] if row else None
