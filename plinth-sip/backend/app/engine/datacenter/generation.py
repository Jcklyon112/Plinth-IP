"""Generation context: nearest baseload + capacity-by-fuel within 25 mi."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text

from app.engine.datacenter.distance import METERS_PER_MILE, meters_to_miles

# "Baseload" for our purposes: nuclear or large gas combined-cycle
# (>=100 MW summer capacity). The spec called out nuclear/CC explicitly.
BASELOAD_FUELS = {"nuclear", "gas"}
BASELOAD_MIN_MW = 100.0


def nearest_baseload_plant(session, lon: float, lat: float) -> Optional[dict]:
    sql = text(
        """
        SELECT name, primary_fuel, summer_capacity_mw, total_mw,
               ST_Distance(
                 geom::geography,
                 ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
               ) AS dist_m
        FROM grid_power_plants
        WHERE primary_fuel = ANY(:fuels)
          AND COALESCE(summer_capacity_mw, total_mw, 0) >= :min_mw
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
        LIMIT 1
        """
    )
    row = session.execute(
        sql,
        {
            "lon": lon,
            "lat": lat,
            "fuels": list(BASELOAD_FUELS),
            "min_mw": BASELOAD_MIN_MW,
        },
    ).first()
    if not row:
        return None
    capacity = float(row.summer_capacity_mw or row.total_mw or 0.0)
    return {
        "name": row.name,
        "fuel": row.primary_fuel,
        "capacityMw": round(capacity, 1),
        "distanceMi": round(meters_to_miles(row.dist_m), 2),
    }


def capacity_within_radius_by_fuel(
    session, lon: float, lat: float, *, radius_mi: float = 25.0
) -> dict[str, float]:
    """Total summer (or total) MW within `radius_mi`, grouped by primary_fuel.

    Returns {fuel: mw_total} only for fuels with non-zero capacity. The
    analyzer fills missing fuels with 0 to satisfy the spec's
    capacityWithin25MiByFuel keys.
    """
    sql = text(
        """
        SELECT primary_fuel,
               SUM(COALESCE(summer_capacity_mw, total_mw, 0))::float AS mw
        FROM grid_power_plants
        WHERE primary_fuel IS NOT NULL
          AND ST_DWithin(
              geom::geography,
              ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
              :radius_m
          )
        GROUP BY primary_fuel
        """
    )
    rows = session.execute(
        sql,
        {
            "lon": lon,
            "lat": lat,
            "radius_m": radius_mi * METERS_PER_MILE,
        },
    ).all()
    return {r.primary_fuel: round(float(r.mw or 0.0), 1) for r in rows}
