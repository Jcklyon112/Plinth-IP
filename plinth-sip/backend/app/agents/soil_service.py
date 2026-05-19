"""
Soil service — USDA SSURGO Soil Data Access integration.

Queries the public SDA REST endpoint for the "ENG - Septic Tank Absorption
Fields" interpretation rating per parcel polygon. The rating drives the
septic_capacity rule when a parcel is not on public sewer.

Pipeline:
  parcel polygon (WGS84) → SDA spatial fn → mukey(s)
  mukey → dominant component → cointerp → "Not limited" / "Somewhat limited"
                                          / "Very limited" / "Not rated"
  parcel → worst-case class across all intersecting mukeys (conservative).

One HTTP request handles a batch of ~50 parcels via CROSS APPLY over a
VALUES table.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry


SDA_URL = "https://sdmdataaccess.nrcs.usda.gov/Tabular/post.rest"

# Severity ordering — higher = worse. Used to pick the worst-case class
# when a parcel intersects multiple soil map units.
_CLASS_RANK = {
    "Very limited": 3,
    "Somewhat limited": 2,
    "Not limited": 1,
    "Not rated": 0,
}

# Practical batch size: each WKT row is ~150-300 chars; 50 fits well under
# the ~100 KB SDA POST body limit and well under the 100s server timeout.
_BATCH_SIZE = 50

_SQL_TEMPLATE = """
WITH parcel_mukeys AS (
    SELECT v.parcel_id, m.mukey
    FROM (VALUES {values}) AS v(parcel_id, wkt)
    CROSS APPLY SDA_Get_Mukey_from_intersection_with_WktWgs84(v.wkt) AS m
),
ranked AS (
    SELECT pm.parcel_id, comp.mukey, mu.muname, comp.compname, comp.comppct_r,
           ci.interphrc AS septic_class,
           ROW_NUMBER() OVER (
               PARTITION BY pm.parcel_id, comp.mukey
               ORDER BY comp.comppct_r DESC
           ) AS rn
    FROM parcel_mukeys pm
    INNER JOIN component comp ON comp.mukey = CAST(pm.mukey AS INT)
    INNER JOIN mapunit mu ON comp.mukey = mu.mukey
    LEFT JOIN cointerp ci ON comp.cokey = ci.cokey
        AND ci.mrulename = 'ENG - Septic Tank Absorption Fields'
        AND ci.ruledepth = 0
)
SELECT parcel_id, mukey, muname, compname AS dominant_component,
       comppct_r AS dominant_pct, septic_class
FROM ranked
WHERE rn = 1
"""


def _shapely_to_wkt_wgs84(geom: BaseGeometry) -> str | None:
    """Convert a Shapely Polygon/MultiPolygon to a single-line WKT string.
    Returns None if the geometry is empty or not a polygonal type."""
    if geom is None or geom.is_empty:
        return None
    try:
        wkt = geom.wkt
    except Exception:
        return None
    # Reject non-polygon types — SDA needs polygonal input
    if not (wkt.startswith("POLYGON") or wkt.startswith("MULTIPOLYGON")):
        return None
    return wkt


def _escape_sql_str(s: str) -> str:
    """Escape single-quotes for T-SQL string literal embedding."""
    return s.replace("'", "''")


def _post_sda(query: str, timeout: int = 90) -> list[list[Any]] | None:
    """POST a SQL query to SDA. Returns the raw 'Table' rows including the
    column-name header row when format is JSON+COLUMNNAME, or None on error."""
    payload = {"query": query, "format": "JSON+COLUMNNAME"}
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                SDA_URL,
                json=payload,
                headers={
                    "User-Agent": "PlinthSIP/1.0 (soil_service)",
                    "Content-Type": "application/json",
                },
            )
            if r.status_code != 200:
                return None
            data = r.json()
    except Exception:  # noqa: BLE001
        return None
    return data.get("Table")


def _query_septic_batch(items: list[tuple[str, str]]) -> dict[str, list[dict]]:
    """
    Run one SDA request for up to _BATCH_SIZE (parcel_id, wkt) pairs.
    Returns parcel_id → list of mukey-component rows.
    """
    if not items:
        return {}
    values_clause = ",\n        ".join(
        f"('{_escape_sql_str(pid)}', '{_escape_sql_str(wkt)}')"
        for pid, wkt in items
    )
    sql = _SQL_TEMPLATE.format(values=values_clause)
    rows = _post_sda(sql)
    if not rows or len(rows) < 2:
        return {}

    header = rows[0]
    try:
        idx = {name: i for i, name in enumerate(header)}
        pid_i = idx["parcel_id"]
        muname_i = idx.get("muname", -1)
        comp_i = idx.get("dominant_component", -1)
        pct_i = idx.get("dominant_pct", -1)
        class_i = idx.get("septic_class", -1)
        mukey_i = idx.get("mukey", -1)
    except KeyError:
        return {}

    out: dict[str, list[dict]] = {}
    for row in rows[1:]:
        pid = row[pid_i]
        out.setdefault(pid, []).append({
            "mukey": row[mukey_i] if mukey_i >= 0 else None,
            "muname": row[muname_i] if muname_i >= 0 else None,
            "dominant_component": row[comp_i] if comp_i >= 0 else None,
            "dominant_pct": row[pct_i] if pct_i >= 0 else None,
            "septic_class": row[class_i] if class_i >= 0 else None,
        })
    return out


def _worst_class(rows: list[dict]) -> tuple[str | None, dict | None]:
    """Pick the worst-severity septic_class from a list of mukey rows.
    Returns (class_string, row_used) or (None, None) if all unrated."""
    if not rows:
        return None, None
    best_rank = -1
    best_row = None
    for r in rows:
        cls = r.get("septic_class") or "Not rated"
        rank = _CLASS_RANK.get(cls, 0)
        if rank > best_rank:
            best_rank = rank
            best_row = r
    if best_row is None:
        return None, None
    return best_row.get("septic_class") or "Not rated", best_row


def annotate_parcels_with_soil(parcels: list[dict]) -> int:
    """
    Mutate each parcel dict in place: set parcel["soil_septic_class"] and
    parcel["soil_septic_detail"] based on SSURGO interpretation.

    Skips parcels lacking a usable polygon. Returns the number of parcels
    successfully annotated with a non-null class. Failures are silent —
    the rule falls back to its lot-area heuristic when soil data is missing.
    """
    if not parcels:
        return 0

    # Collect (parcel_id, wkt) pairs, skipping parcels with bad/missing geom
    items: list[tuple[str, str]] = []
    by_id: dict[str, dict] = {}
    for p in parcels:
        pid = p.get("parcel_id")
        if not pid:
            continue
        geom = p.get("geometry_shapely")
        if geom is None:
            geojson = p.get("geometry") or p.get("geometry_geojson")
            if geojson:
                try:
                    from shapely.geometry import shape as shapely_shape
                    geom = shapely_shape(geojson)
                except Exception:
                    geom = None
        wkt = _shapely_to_wkt_wgs84(geom) if geom else None
        if not wkt:
            continue
        items.append((pid, wkt))
        by_id[pid] = p

    if not items:
        return 0

    successes = 0
    for start in range(0, len(items), _BATCH_SIZE):
        batch = items[start:start + _BATCH_SIZE]
        result = _query_septic_batch(batch)
        for pid, rows in result.items():
            parcel = by_id.get(pid)
            if not parcel:
                continue
            cls, used = _worst_class(rows)
            parcel["soil_septic_class"] = cls
            parcel["soil_septic_detail"] = {
                "rows": rows,
                "worst": used,
                "source": "USDA SSURGO (Soil Data Access)",
            }
            if cls:
                successes += 1
    return successes
