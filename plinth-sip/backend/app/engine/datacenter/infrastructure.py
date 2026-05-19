"""Supporting infrastructure: fiber, gas, FEMA flood, wetlands.

Phase 2 wires up the read paths and tolerates missing layers (Phase 3
loads fiber/gas; FEMA may already be in `overlays`; USFWS NWI is queried
on demand). The analyzer surfaces None values as "unknown" with a
warning.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

from app.engine.datacenter.distance import METERS_PER_MILE, meters_to_miles

log = logging.getLogger(__name__)


# --- fiber ------------------------------------------------------------

def nearest_fiber(session, lon: float, lat: float) -> Optional[dict]:
    """Nearest fiber feature with snapped coordinate for the analysis line."""
    if not _table_has_rows(session, "grid_fiber_routes"):
        return None
    sql = text(
        """
        SELECT source_label, carrier,
               ST_X(ST_ClosestPoint(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)))::float AS snap_lon,
               ST_Y(ST_ClosestPoint(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)))::float AS snap_lat,
               ST_Distance(
                 geom::geography,
                 ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
               ) AS dist_m
        FROM grid_fiber_routes
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
        LIMIT 1
        """
    )
    row = session.execute(sql, {"lon": lon, "lat": lat}).first()
    if not row:
        return None
    return {
        "sourceLabel": row.source_label,
        "carrier": row.carrier,
        "distanceMi": round(meters_to_miles(row.dist_m), 2),
        "coords": [float(row.snap_lon), float(row.snap_lat)],
    }


def nearest_fiber_distance_mi(session, lon: float, lat: float) -> Optional[float]:
    """Convenience wrapper retained for callers that only need the distance."""
    info = nearest_fiber(session, lon, lat)
    return info["distanceMi"] if info else None


# --- gas pipelines ----------------------------------------------------

def nearest_gas_pipeline(session, lon: float, lat: float) -> Optional[dict]:
    if not _table_has_rows(session, "grid_gas_pipelines"):
        return None
    sql = text(
        """
        SELECT operator,
               ST_X(ST_ClosestPoint(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)))::float AS snap_lon,
               ST_Y(ST_ClosestPoint(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)))::float AS snap_lat,
               ST_Distance(
                 geom::geography,
                 ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
               ) AS dist_m
        FROM grid_gas_pipelines
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
        LIMIT 1
        """
    )
    row = session.execute(sql, {"lon": lon, "lat": lat}).first()
    if not row:
        return None
    return {
        "operator": row.operator,
        "distanceMi": round(meters_to_miles(row.dist_m), 2),
        "coords": [float(row.snap_lon), float(row.snap_lat)],
    }


def nearest_gas_pipeline_distance_mi(session, lon: float, lat: float) -> Optional[float]:
    info = nearest_gas_pipeline(session, lon, lat)
    return info["distanceMi"] if info else None


# --- FEMA flood -------------------------------------------------------

def flood_zone_at_point(session, lon: float, lat: float) -> Optional[str]:
    """Return the FEMA flood-zone label at the point, or None if no
    flood overlay rows are loaded.

    Uses the existing `overlays` table populated by the ADU pipeline
    (`overlay_type = 'flood_zone'`). The `label` column carries the
    zone code (e.g., "AE", "X", "VE").
    """
    sql = text(
        """
        SELECT label
        FROM overlays
        WHERE overlay_type = 'flood_zone'
          AND active = TRUE
          AND geom IS NOT NULL
          AND ST_Contains(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
        ORDER BY ST_Area(geom) ASC
        LIMIT 1
        """
    )
    row = session.execute(sql, {"lon": lon, "lat": lat}).first()
    return row.label if row else None


# --- wetlands (USFWS NWI; on-demand) ----------------------------------

def wetland_coverage_pct(session, parcel_geom_wkt: Optional[str]) -> Optional[float]:
    """Wetland coverage % over the parcel polygon.

    Hits USFWS NWI on demand; result lands in the analyzer cache so a
    re-click is free until the grid_data_version changes. `session` is
    accepted for signature stability with the rest of this module but
    isn't used (NWI is fetched directly, not stored in PostGIS).
    """
    from app.engine.datacenter.nwi import wetland_coverage_pct as _nwi_pct
    if not parcel_geom_wkt:
        return None
    try:
        return _nwi_pct(parcel_geom_wkt)
    except Exception:
        log.exception("USFWS NWI coverage lookup failed")
        return None


# --- helper -----------------------------------------------------------

def _table_has_rows(session, table: str) -> bool:
    sql = text(f"SELECT 1 FROM {table} LIMIT 1")
    return session.execute(sql).first() is not None
