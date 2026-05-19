"""Data-center feasibility router.

Two endpoints:

  POST /analysis/datacenter
       body: { municipality_id, parcel_id, options? }
       Cached per (parcel_id, municipality_id, grid_data_version).

  POST /analysis/datacenter/by-shape
       body: { geojson, label? }
       Stateless; no DB write, no cache.

Plus helper endpoints under `/grid/*` for bbox-filtered map layers,
which the Phase 4 frontend toggles consume:

  GET  /grid/substations
  GET  /grid/transmission-lines
  GET  /grid/power-plants
  GET  /grid/iso-rto
  GET  /grid/refresh-status
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.engine.datacenter.analyzer import (
    analyze_parcel,
    analyze_shape,
    grid_data_version,
)


router = APIRouter()


# --- /analysis/datacenter --------------------------------------------

class DcAnalysisRequest(BaseModel):
    municipality_id: str = Field(..., description="Municipality id (e.g. ma_acton)")
    parcel_id: str = Field(..., description="Source-system parcel id (LOC_ID etc.)")
    use_cache: bool = True


class DcShapeAnalysisRequest(BaseModel):
    geojson: dict = Field(..., description="A GeoJSON Polygon or MultiPolygon geometry.")
    label: Optional[str] = None


@router.post("/datacenter")
def analyze_datacenter(body: DcAnalysisRequest, db: Session = Depends(get_db)) -> dict:
    try:
        result = analyze_parcel(
            db,
            parcel_id=body.parcel_id,
            municipality_id=body.municipality_id,
            use_cache=body.use_cache,
        )
        if body.use_cache:
            db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/datacenter/by-shape")
def analyze_datacenter_shape(body: DcShapeAnalysisRequest, db: Session = Depends(get_db)) -> dict:
    return analyze_shape(db, geojson=body.geojson, label=body.label)


# --- /grid/* helpers (bbox-filtered) ---------------------------------

def _parse_bbox(bbox: Optional[str]) -> Optional[tuple[float, float, float, float]]:
    if not bbox:
        return None
    parts = [p.strip() for p in bbox.split(",")]
    if len(parts) != 4:
        raise HTTPException(400, "bbox must be 'w,s,e,n'")
    try:
        w, s, e, n = (float(p) for p in parts)
    except ValueError:
        raise HTTPException(400, "bbox values must be floats")
    return w, s, e, n


def _bbox_filter_clause() -> str:
    return (
        "ST_Intersects(geom, ST_MakeEnvelope(:w, :s, :e, :n, 4326))"
    )


def _features_query(db: Session, sql: str, params: dict) -> dict:
    rows = db.execute(text(sql), params).mappings().all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": r["geometry"],
                "properties": {k: v for k, v in r.items() if k != "geometry"},
            }
            for r in rows
        ],
        "count": len(rows),
    }


grid_router = APIRouter()


@grid_router.get("/substations")
def grid_substations(
    bbox: Optional[str] = Query(None, description="w,s,e,n in WGS84"),
    min_voltage_kv: Optional[int] = Query(None),
    limit: int = Query(5000, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    bb = _parse_bbox(bbox)
    where = ["TRUE"]
    params: dict = {"lim": limit}
    if bb is not None:
        where.append(_bbox_filter_clause())
        params["w"], params["s"], params["e"], params["n"] = bb
    if min_voltage_kv is not None:
        where.append("max_voltage_kv >= :min_kv")
        params["min_kv"] = min_voltage_kv

    sql = (
        f"""
        SELECT name, operator, type, status,
               max_voltage_kv, min_voltage_kv, lines_count,
               ST_AsGeoJSON(geom)::json AS geometry
        FROM grid_substations
        WHERE {' AND '.join(where)}
        LIMIT :lim
        """
    )
    return _features_query(db, sql, params)


@grid_router.get("/transmission-lines")
def grid_transmission_lines(
    bbox: Optional[str] = Query(None),
    min_voltage_kv: Optional[int] = Query(None),
    limit: int = Query(5000, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    bb = _parse_bbox(bbox)
    where = ["TRUE"]
    params: dict = {"lim": limit}
    if bb is not None:
        where.append(_bbox_filter_clause())
        params["w"], params["s"], params["e"], params["n"] = bb
    if min_voltage_kv is not None:
        where.append("voltage_kv >= :min_kv")
        params["min_kv"] = min_voltage_kv

    sql = (
        f"""
        SELECT owner, voltage_kv, voltage_class, type, status,
               ST_AsGeoJSON(geom)::json AS geometry
        FROM grid_transmission_lines
        WHERE {' AND '.join(where)}
        LIMIT :lim
        """
    )
    return _features_query(db, sql, params)


@grid_router.get("/power-plants")
def grid_power_plants(
    bbox: Optional[str] = Query(None),
    fuel: Optional[str] = Query(None, description="primary_fuel filter (e.g. nuclear, gas)"),
    limit: int = Query(2000, ge=1, le=10000),
    db: Session = Depends(get_db),
):
    bb = _parse_bbox(bbox)
    where = ["TRUE"]
    params: dict = {"lim": limit}
    if bb is not None:
        where.append(_bbox_filter_clause())
        params["w"], params["s"], params["e"], params["n"] = bb
    if fuel is not None:
        where.append("primary_fuel = :fuel")
        params["fuel"] = fuel

    sql = (
        f"""
        SELECT name, operator, primary_fuel, total_mw, summer_capacity_mw, status,
               ST_AsGeoJSON(geom)::json AS geometry
        FROM grid_power_plants
        WHERE {' AND '.join(where)}
        LIMIT :lim
        """
    )
    return _features_query(db, sql, params)


@grid_router.get("/iso-rto")
def grid_iso_rto(
    bbox: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    bb = _parse_bbox(bbox)
    where = ["TRUE"]
    params: dict = {}
    if bb is not None:
        where.append(_bbox_filter_clause())
        params["w"], params["s"], params["e"], params["n"] = bb

    sql = (
        f"""
        SELECT ba_code, ba_name, iso_rto,
               ST_AsGeoJSON(geom)::json AS geometry
        FROM grid_balancing_authorities
        WHERE {' AND '.join(where)}
        """
    )
    return _features_query(db, sql, params)


@grid_router.get("/service-territory")
def grid_service_territory(
    utility_id_eia: Optional[int] = Query(None),
    bbox: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    if utility_id_eia is None and not bbox:
        raise HTTPException(400, "must provide either utility_id_eia or bbox")
    bb = _parse_bbox(bbox)
    where = ["TRUE"]
    params: dict = {}
    if utility_id_eia is not None:
        where.append("utility_id_eia = :uid")
        params["uid"] = utility_id_eia
    if bb is not None:
        where.append(_bbox_filter_clause())
        params["w"], params["s"], params["e"], params["n"] = bb
    sql = (
        f"""
        SELECT utility_id_eia, utility_name, holding_company, state,
               ST_AsGeoJSON(geom)::json AS geometry
        FROM grid_service_territories
        WHERE {' AND '.join(where)}
        """
    )
    return _features_query(db, sql, params)


@grid_router.get("/refresh-status")
def grid_refresh_status(db: Session = Depends(get_db)) -> dict:
    """Per-layer last refresh timestamp + the cache invalidation key.

    Used by the frontend to show a "data freshness" indicator and by ops
    to know whether a re-load is needed.
    """
    rows = db.execute(
        text(
            """
            SELECT layer_name, last_refresh_at, feature_count, source_url, source_label, notes
            FROM grid_refresh_metadata
            ORDER BY layer_name
            """
        )
    ).mappings().all()
    return {
        "layers": [dict(r) for r in rows],
        "grid_data_version": grid_data_version(db),
    }
