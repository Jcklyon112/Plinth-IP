"""Top-level orchestrator: parcel -> spec'd data-center feasibility JSON.

Returns the exact shape documented in the task spec:

    {
      parcelId, overallScore, scoreRationale, tierFit, gatingIssues,
      grid: {...}, generation: {...}, power: {...}, infrastructure: {...},
      zoning, warnings
    }

Caches results in `parcel_datacenter_analyses` keyed by
(parcel_id, municipality_id, grid_data_version). The version is a
deterministic short hash of `grid_refresh_metadata.last_refresh_at`
across the layers we consulted, so any layer refresh invalidates the
prior cache rows.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from app.engine.datacenter import (
    generation as gen_mod,
    infrastructure as infra_mod,
    iso as iso_mod,
    land as land_mod,
    power_cost as cost_mod,
    proximity as prox_mod,
    recommendation as rec_mod,
    scoring as score_mod,
)
from app.engine.datacenter.distance import meters_to_miles

log = logging.getLogger(__name__)

# Verbatim caveats per the task spec - these go into `warnings` on every
# report, not just the marginal ones.
STANDARD_WARNINGS = [
    "Available substation capacity is NOT modeled - verify with the serving utility before proceeding.",
    "Interconnection queue position is NOT live - see ISO link for current status.",
    "Industrial rates are utility averages from EIA 861 - actual large-load tariffs may differ significantly.",
]

# Layers consulted by the analyzer; influences the cache invalidation key.
_LAYERS_USED = [
    "grid_substations",
    "grid_transmission_lines",
    "grid_power_plants",
    "grid_balancing_authorities",
    "grid_service_territories",
    "eia_industrial_rates",
    "grid_gas_pipelines",
    "grid_fiber_routes",
]


# --- cache invalidation key ------------------------------------------

def grid_data_version(session) -> str:
    """Short deterministic hash of last_refresh_at for the layers we use.

    Returns a fixed marker when the metadata table is empty so that a
    fresh DB still has a stable key.
    """
    sql = text(
        """
        SELECT layer_name, last_refresh_at
        FROM grid_refresh_metadata
        WHERE layer_name = ANY(:names)
        ORDER BY layer_name
        """
    )
    rows = session.execute(sql, {"names": _LAYERS_USED}).all()
    if not rows:
        return "no-grid-data"
    payload = "|".join(
        f"{r.layer_name}:{r.last_refresh_at.isoformat() if r.last_refresh_at else 'never'}"
        for r in rows
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# --- parcel input handling -------------------------------------------

def _resolve_parcel(session, *, parcel_id: str, municipality_id: str) -> dict:
    """Pull the parcel's metadata + centroid from the DB.

    Returns a dict with: parcel_id, municipality_id, address, zoning_code,
    land_use_type, lot_area_sqft, lon, lat. Raises ValueError if the
    parcel or its geometry is missing.
    """
    sql = text(
        """
        SELECT p.parcel_id, p.municipality_id, p.address, p.zoning_code,
               p.land_use_type, p.lot_area_sqft,
               ST_X(ST_Centroid(pg.geom))::float AS lon,
               ST_Y(ST_Centroid(pg.geom))::float AS lat,
               ST_AsText(pg.geom) AS wkt
        FROM parcels p
        JOIN parcel_geometries pg
          ON p.parcel_id = pg.parcel_id AND p.municipality_id = pg.municipality_id
        WHERE p.parcel_id = :pid AND p.municipality_id = :mid
        LIMIT 1
        """
    )
    row = session.execute(sql, {"pid": parcel_id, "mid": municipality_id}).first()
    if not row:
        raise ValueError(f"Parcel {municipality_id}/{parcel_id} not found or has no geometry.")
    return {
        "parcel_id": row.parcel_id,
        "municipality_id": row.municipality_id,
        "address": row.address,
        "zoning_code": row.zoning_code,
        "land_use_type": row.land_use_type,
        "lot_area_sqft": float(row.lot_area_sqft) if row.lot_area_sqft is not None else None,
        "lon": float(row.lon),
        "lat": float(row.lat),
        "geom_wkt": row.wkt,
    }


# --- main entry points -----------------------------------------------

def analyze_parcel(
    session,
    *,
    parcel_id: str,
    municipality_id: str,
    use_cache: bool = True,
) -> dict:
    """Analyze a DB-resident parcel. Caches the result by grid data version."""
    # Cache hit?
    version = grid_data_version(session)
    if use_cache:
        cached = session.execute(
            text(
                """
                SELECT result FROM parcel_datacenter_analyses
                WHERE parcel_id = :pid AND municipality_id = :mid AND grid_data_version = :v
                """
            ),
            {"pid": parcel_id, "mid": municipality_id, "v": version},
        ).first()
        if cached:
            log.info("DC analysis cache hit for %s/%s @ %s", municipality_id, parcel_id, version)
            return cached.result

    parcel = _resolve_parcel(session, parcel_id=parcel_id, municipality_id=municipality_id)
    result = _build_report(session, parcel)

    # Store
    if use_cache:
        session.execute(
            text(
                """
                INSERT INTO parcel_datacenter_analyses
                    (parcel_id, municipality_id, grid_data_version, result)
                VALUES (:pid, :mid, :v, CAST(:r AS jsonb))
                ON CONFLICT (parcel_id, municipality_id, grid_data_version)
                DO UPDATE SET result = EXCLUDED.result, computed_at = NOW()
                """
            ),
            {"pid": parcel_id, "mid": municipality_id, "v": version, "r": json.dumps(result)},
        )
    return result


def analyze_shape(session, *, geojson: dict, label: Optional[str] = None) -> dict:
    """Analyze an arbitrary polygon - no DB persistence, no cache."""
    from shapely.geometry import shape

    geom = shape(geojson)
    centroid = geom.centroid
    parcel = {
        "parcel_id": label or "ad-hoc-shape",
        "municipality_id": "",
        "address": label,
        "zoning_code": None,
        "land_use_type": None,
        "lot_area_sqft": None,
        "lon": float(centroid.x),
        "lat": float(centroid.y),
        "geom_wkt": geom.wkt,
    }
    return _build_report(session, parcel)


# --- assembly --------------------------------------------------------

def _build_report(session, parcel: dict) -> dict:
    lon = parcel["lon"]
    lat = parcel["lat"]
    warnings: list[str] = list(STANDARD_WARNINGS)

    # 1) Grid context -----------------------------------------------------
    nearest_sub = prox_mod.nearest_substation(session, lon, lat)
    nearest_tx_sub = prox_mod.nearest_substation(
        session, lon, lat, min_voltage_kv=prox_mod.TRANSMISSION_VOLT_THRESHOLD_KV
    )
    subs_within_5 = prox_mod.substations_within_radius(
        session, lon, lat, radius_mi=5.0, min_voltage_kv=None
    )
    nearest_line = prox_mod.nearest_transmission_line(session, lon, lat)
    has_230_within_1 = prox_mod.has_line_within(session, lon, lat, radius_mi=1.0, min_voltage_kv=230)
    corridor_count = prox_mod.transmission_corridor_count(session, lon, lat, radius_mi=5.0)
    dual_feed = prox_mod.dual_feed_feasible(session, lon, lat, radius_mi=5.0)

    iso_name = iso_mod.iso_for_point(session, lon, lat)
    iso_entry = iso_mod.iso_metadata_entry(iso_name) if iso_name else {"name": "NON-ISO"}
    iso_block = {
        "name": iso_entry.get("name") or "NON-ISO",
        "fullName": iso_entry.get("full_name"),
        "queueDashboardUrl": iso_entry.get("queue_dashboard_url"),
        "typicalQueueTimeline": iso_entry.get("typical_queue_timeline"),
        "currentPosture": iso_entry.get("current_posture"),
    }

    grid_block = {
        "nearestSubstation": nearest_sub,
        "nearestTransmissionSubstation": nearest_tx_sub,
        "substationsWithin5Mi": subs_within_5,
        "nearestTransmissionLine": nearest_line,
        "has230kvLineWithin1Mi": has_230_within_1,
        "transmissionCorridorsWithin5Mi": corridor_count,
        "dualFeedFeasible": dual_feed,
        "iso": iso_block,
    }

    if not nearest_sub:
        warnings.append("No substation data loaded - run data/grid/refresh_all.py.")
    if not iso_name:
        warnings.append("Parcel falls outside loaded balancing-authority polygons.")

    # 2) Generation context -----------------------------------------------
    nearest_baseload = gen_mod.nearest_baseload_plant(session, lon, lat)
    capacity_by_fuel_raw = gen_mod.capacity_within_radius_by_fuel(session, lon, lat, radius_mi=25.0)
    # Normalize to all expected fuels per spec, even when zero.
    expected_fuels = ("nuclear", "gas", "wind", "solar", "coal", "hydro", "oil", "biomass", "geothermal", "battery", "other")
    capacity_by_fuel = {f: float(capacity_by_fuel_raw.get(f, 0.0)) for f in expected_fuels}

    gen_block = {
        "nearestBaseload": nearest_baseload,
        "capacityWithin25MiByFuel": capacity_by_fuel,
    }

    # 3) Power cost -------------------------------------------------------
    power_block = cost_mod.power_cost_for_point(session, lon, lat)
    if power_block.get("utility") is None:
        warnings.append("Utility service territory data not loaded - power cost unknown.")

    # 4) Infrastructure ---------------------------------------------------
    nearest_fiber = infra_mod.nearest_fiber(session, lon, lat)
    nearest_gas = infra_mod.nearest_gas_pipeline(session, lon, lat)
    flood = infra_mod.flood_zone_at_point(session, lon, lat)
    wetlands = infra_mod.wetland_coverage_pct(session, parcel.get("geom_wkt"))
    acres = land_mod.sqft_to_acres(parcel.get("lot_area_sqft"))

    infra_block = {
        "fiberDistanceMi": nearest_fiber["distanceMi"] if nearest_fiber else None,
        "nearestFiber": nearest_fiber,
        "gasPipelineDistanceMi": nearest_gas["distanceMi"] if nearest_gas else None,
        "nearestGasPipeline": nearest_gas,
        "floodZone": flood,
        "wetlandCoveragePct": wetlands,
        "acreage": round(acres, 2) if acres is not None else None,
        "acreageTier": land_mod.acreage_tier(acres),
    }

    if nearest_fiber is None:
        warnings.append("Fiber data not loaded - proximity unknown.")
    if nearest_gas is None:
        warnings.append("Gas pipeline data not loaded - proximity unknown.")
    if wetlands is None:
        warnings.append("Wetland coverage unavailable - USFWS NWI lookup failed or parcel geometry missing.")

    # 5) Land / zoning ----------------------------------------------------
    zoning = land_mod.zoning_compatibility(parcel.get("zoning_code"), parcel.get("land_use_type"))

    # 6) Score ------------------------------------------------------------
    pre_score_report = {
        "grid": grid_block,
        "generation": gen_block,
        "power": power_block,
        "infrastructure": infra_block,
        "zoning": zoning,
    }
    s = score_mod.score_report(pre_score_report)

    # 7) Final shape -------------------------------------------------------
    report = {
        "parcelId": parcel.get("parcel_id"),
        "municipalityId": parcel.get("municipality_id"),
        "address": parcel.get("address"),
        # Parcel centroid in WGS84; the frontend anchors the analysis
        # lines (parcel -> nearest substation, etc.) here.
        "parcelCentroid": [lon, lat],
        "computedAt": datetime.now(timezone.utc).isoformat(),
        "overallScore": s.letter,
        "compositeScore": s.composite,
        "subscores": s.subscores,
        "scoreRationale": s.rationale,
        "tierFit": land_mod.tier_fit(acres),
        "gatingIssues": s.gating_issues,
        "grid": grid_block,
        "generation": gen_block,
        "power": power_block,
        "infrastructure": infra_block,
        "zoning": zoning,
        "warnings": warnings,
    }
    # 8) Anchor-component recommendation + Rhino-bound readout ------------
    # Both are pure functions of the report dict above; we attach them so
    # the GH bake side can pull them out without re-running any scoring.
    report["recommendation"] = rec_mod.recommend_anchor_component(report)
    report["recommendationReadout"] = rec_mod.format_parcel_readout(report)
    return report
