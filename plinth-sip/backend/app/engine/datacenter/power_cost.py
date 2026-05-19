"""Power cost: utility lookup + EIA Form 861 industrial rate.

Phase 2 wires up the SQL; Phase 3 fills `grid_service_territories` and
`eia_industrial_rates`. Until those are loaded, this module returns
`{"utility": None, "rate_cents_per_kwh": None, "rate_tier": None}` and
the analyzer surfaces "unknown" with a warning.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text


def rate_tier(cents_per_kwh: Optional[float]) -> Optional[str]:
    """Bucket per the spec.

    Low      <6 c/kWh
    Medium   6-10 c/kWh
    High     10-14 c/kWh
    Very High >14 c/kWh
    """
    if cents_per_kwh is None:
        return None
    if cents_per_kwh < 6.0:
        return "Low"
    if cents_per_kwh < 10.0:
        return "Medium"
    if cents_per_kwh < 14.0:
        return "High"
    return "Very High"


def utility_for_point(session, lon: float, lat: float) -> Optional[dict]:
    """Return the utility serving (lon, lat), or None if no service-territory
    polygon contains the point (or the table is empty)."""
    sql = text(
        """
        SELECT utility_id_eia, utility_name, holding_company, state
        FROM grid_service_territories
        WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
        ORDER BY ST_Area(geom) ASC  -- prefer smaller (more specific) polygon
        LIMIT 1
        """
    )
    row = session.execute(sql, {"lon": lon, "lat": lat}).first()
    if not row:
        return None
    return {
        "utility_id_eia": row.utility_id_eia,
        "utility_name": row.utility_name,
        "holding_company": row.holding_company,
        "state": row.state,
    }


def latest_industrial_rate(session, utility_id_eia: int) -> Optional[float]:
    sql = text(
        """
        SELECT rate_cents_per_kwh
        FROM eia_industrial_rates
        WHERE utility_id_eia = :uid
          AND sector = 'industrial'
          AND rate_cents_per_kwh IS NOT NULL
        ORDER BY year DESC
        LIMIT 1
        """
    )
    row = session.execute(sql, {"uid": utility_id_eia}).first()
    return float(row.rate_cents_per_kwh) if row else None


def power_cost_for_point(session, lon: float, lat: float) -> dict:
    util = utility_for_point(session, lon, lat)
    if not util:
        return {
            "utility": None,
            "industrialRateCentsPerKwh": None,
            "rateTier": None,
        }
    rate = (
        latest_industrial_rate(session, util["utility_id_eia"])
        if util["utility_id_eia"] is not None
        else None
    )
    return {
        "utility": util["utility_name"],
        "industrialRateCentsPerKwh": round(rate, 2) if rate is not None else None,
        "rateTier": rate_tier(rate),
    }
