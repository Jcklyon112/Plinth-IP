"""DB-backed proximity helpers used by the analyzer.

All distance math is done in PostGIS using the geography cast so results
come back in meters; we convert to miles in `analyzer.py`. The KNN
operator `<->` is GIST-aware and gives nearest-neighbor in O(log n).
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

from app.engine.datacenter.distance import METERS_PER_MILE, meters_to_miles

log = logging.getLogger(__name__)

TRANSMISSION_VOLT_THRESHOLD_KV = 115


# --- substations ------------------------------------------------------

def nearest_substation(session, lon: float, lat: float, *, min_voltage_kv: Optional[int] = None) -> Optional[dict]:
    """Nearest substation to (lon, lat).

    Returns a dict with name/operator/maxVoltageKv/distanceMi or None
    when the substations table is empty.
    """
    where = "TRUE"
    params = {"lon": lon, "lat": lat}
    if min_voltage_kv is not None:
        where = "max_voltage_kv >= :min_kv"
        params["min_kv"] = min_voltage_kv

    sql = text(
        f"""
        SELECT name, operator, max_voltage_kv,
               ST_X(geom)::float AS lon, ST_Y(geom)::float AS lat,
               ST_Distance(
                 geom::geography,
                 ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
               ) AS dist_m
        FROM grid_substations
        WHERE {where}
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
        LIMIT 1
        """
    )
    row = session.execute(sql, params).first()
    if not row:
        return None
    return {
        "name": row.name,
        "operator": row.operator,
        "maxVoltageKv": row.max_voltage_kv,
        "distanceMi": round(meters_to_miles(row.dist_m), 2),
        "coords": [float(row.lon), float(row.lat)],
    }


def substations_within_radius(
    session, lon: float, lat: float, *, radius_mi: float, min_voltage_kv: Optional[int] = None
) -> list[dict]:
    """All substations within `radius_mi`, ranked by (voltage desc, distance asc)."""
    where = "ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :radius_m)"
    params = {
        "lon": lon,
        "lat": lat,
        "radius_m": radius_mi * METERS_PER_MILE,
    }
    if min_voltage_kv is not None:
        where += " AND max_voltage_kv >= :min_kv"
        params["min_kv"] = min_voltage_kv

    sql = text(
        f"""
        SELECT id, name, operator, max_voltage_kv,
               ST_Distance(
                 geom::geography,
                 ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
               ) AS dist_m
        FROM grid_substations
        WHERE {where}
        ORDER BY max_voltage_kv DESC NULLS LAST, dist_m ASC
        """
    )
    rows = session.execute(sql, params).all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "operator": r.operator,
            "maxVoltageKv": r.max_voltage_kv,
            "distanceMi": round(meters_to_miles(r.dist_m), 2),
        }
        for r in rows
    ]


# --- transmission lines ----------------------------------------------

def nearest_transmission_line(session, lon: float, lat: float, *, min_voltage_kv: Optional[int] = TRANSMISSION_VOLT_THRESHOLD_KV) -> Optional[dict]:
    where = "TRUE"
    params = {"lon": lon, "lat": lat}
    if min_voltage_kv is not None:
        where = "voltage_kv >= :min_kv"
        params["min_kv"] = min_voltage_kv

    # ClosestPoint gives the snap-point on the line nearest to the parcel
    # - that's what we draw the analysis line to.
    sql = text(
        f"""
        SELECT owner, voltage_kv,
               ST_X(ST_ClosestPoint(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)))::float AS snap_lon,
               ST_Y(ST_ClosestPoint(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)))::float AS snap_lat,
               ST_Distance(
                 geom::geography,
                 ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
               ) AS dist_m
        FROM grid_transmission_lines
        WHERE {where}
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
        LIMIT 1
        """
    )
    row = session.execute(sql, params).first()
    if not row:
        return None
    return {
        "owner": row.owner,
        "voltageKv": row.voltage_kv,
        "distanceMi": round(meters_to_miles(row.dist_m), 2),
        "coords": [float(row.snap_lon), float(row.snap_lat)],
    }


def has_line_within(
    session, lon: float, lat: float, *, radius_mi: float, min_voltage_kv: int
) -> bool:
    sql = text(
        """
        SELECT 1
        FROM grid_transmission_lines
        WHERE voltage_kv >= :min_kv
          AND ST_DWithin(
              geom::geography,
              ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
              :radius_m
          )
        LIMIT 1
        """
    )
    return session.execute(
        sql,
        {
            "lon": lon,
            "lat": lat,
            "radius_m": radius_mi * METERS_PER_MILE,
            "min_kv": min_voltage_kv,
        },
    ).first() is not None


def transmission_corridor_count(
    session, lon: float, lat: float, *, radius_mi: float = 5.0, min_voltage_kv: int = TRANSMISSION_VOLT_THRESHOLD_KV
) -> int:
    """Distinct transmission corridors within `radius_mi`.

    The user spec asked for "count of distinct transmission corridors";
    we approximate by counting distinct (owner, voltage_class) pairs in
    the buffer. Using the owner alone misses the case of one utility
    operating multiple parallel circuits at different voltages on
    different ROWs, which is the textbook dual-feed setup.
    """
    sql = text(
        """
        SELECT COUNT(DISTINCT (COALESCE(owner, ''), COALESCE(voltage_class, '')))
        FROM grid_transmission_lines
        WHERE voltage_kv >= :min_kv
          AND ST_DWithin(
              geom::geography,
              ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
              :radius_m
          )
        """
    )
    n = session.execute(
        sql,
        {
            "lon": lon,
            "lat": lat,
            "radius_m": radius_mi * METERS_PER_MILE,
            "min_kv": min_voltage_kv,
        },
    ).scalar()
    return int(n or 0)


# --- dual feed --------------------------------------------------------

def is_dual_feed_from_line_sets(sub_line_sets: list[set[str]]) -> bool:
    """Pure decision rule, separate from the DB I/O.

    Given the line-id sets connected to the two nearest >=115kV
    substations, the parcel can dual-feed iff:
      * we have >=2 substations,
      * each has a non-empty connected-line set,
      * the two sets are disjoint.

    Disjoint sets mean the substations don't share a transmission line,
    which is the textbook indicator that they sit on different corridors.
    """
    if len(sub_line_sets) < 2:
        return False
    a, b = sub_line_sets[0], sub_line_sets[1]
    if not a or not b:
        return False
    return a.isdisjoint(b)


def dual_feed_feasible(
    session, lon: float, lat: float, *, radius_mi: float = 5.0
) -> bool:
    """Approximate dual-feed feasibility per the spec.

    Definition: two distinct transmission substations within `radius_mi`,
    connected to non-overlapping transmission line sets. We approximate
    "connected to" by "has a line passing within 0.5 mi", since HIFLD
    doesn't ship explicit substation-line topology. The decision rule
    is delegated to `is_dual_feed_from_line_sets` for testability.
    """
    subs = session.execute(
        text(
            """
            SELECT id
            FROM grid_substations
            WHERE max_voltage_kv >= :min_kv
              AND ST_DWithin(
                  geom::geography,
                  ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                  :radius_m
              )
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
            LIMIT 2
            """
        ),
        {
            "lon": lon,
            "lat": lat,
            "min_kv": TRANSMISSION_VOLT_THRESHOLD_KV,
            "radius_m": radius_mi * METERS_PER_MILE,
        },
    ).all()
    if len(subs) < 2:
        return False

    # Lines within 0.5 mi of each substation.
    line_buffer_m = 0.5 * METERS_PER_MILE
    sub_lines: list[set[str]] = []
    for s in subs:
        lines = session.execute(
            text(
                """
                SELECT l.id
                FROM grid_transmission_lines l, grid_substations s
                WHERE s.id = :sid
                  AND ST_DWithin(l.geom::geography, s.geom::geography, :buf_m)
                """
            ),
            {"sid": s.id, "buf_m": line_buffer_m},
        ).all()
        sub_lines.append({str(r.id) for r in lines})

    return is_dual_feed_from_line_sets(sub_lines)
