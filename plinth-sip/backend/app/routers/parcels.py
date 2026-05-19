import json
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, text
from typing import Optional
from pydantic import BaseModel
import csv
import io
from app.database import get_db
from app.models.parcel import Parcel, ParcelGeometry, ParcelRuleResult, ParcelScore, ParcelAnalystRecord
from app.models.municipality import MunicipalityConfig
from app.engine.runner import normalize_zoning_code
from geoalchemy2.functions import ST_AsGeoJSON

router = APIRouter()


class ShapeQuery(BaseModel):
    municipality_id: str = ""
    geojson_shape: dict
    limit: int = 2000


@router.post("/within-shape")
def parcels_within_shape(
    body: ShapeQuery,
    db: Session = Depends(get_db),
):
    """
    Return all parcels whose geometry intersects a given GeoJSON polygon.
    Used by the map draw-to-select tool.
    """
    shape_json = json.dumps(body.geojson_shape)

    sql = text("""
        SELECT p.parcel_id, p.municipality_id, p.address, p.owner_name,
               p.zoning_code, p.lot_area_sqft, p.land_use_type,
               p.existing_structure_count,
               ps.score, ps.tier, ps.score_breakdown, ps.confidence,
               ST_AsGeoJSON(pg.geom)::json as geometry
        FROM parcels p
        JOIN parcel_geometries pg
          ON p.parcel_id = pg.parcel_id AND p.municipality_id = pg.municipality_id
        LEFT JOIN parcel_scores ps
          ON p.parcel_id = ps.parcel_id AND p.municipality_id = ps.municipality_id
        WHERE ST_Intersects(
            pg.geom,
            ST_SetSRID(ST_GeomFromGeoJSON(:shape_geojson), 4326)
        )
        AND (:muni_id = '' OR p.municipality_id = :muni_id)
        ORDER BY ps.score DESC NULLS LAST
        LIMIT :lim
    """)

    rows = db.execute(sql, {
        "shape_geojson": shape_json,
        "muni_id": body.municipality_id,
        "lim": body.limit,
    }).mappings().all()

    features = []
    for row in rows:
        props = {
            "parcel_id": row["parcel_id"],
            "municipality_id": row["municipality_id"],
            "address": row["address"],
            "owner_name": row["owner_name"],
            "zoning_code": row["zoning_code"],
            "lot_area_sqft": float(row["lot_area_sqft"]) if row["lot_area_sqft"] else None,
            "land_use_type": row["land_use_type"],
            "existing_structure_count": row["existing_structure_count"],
            "score": float(row["score"]) if row["score"] else None,
            "tier": row["tier"],
            "score_breakdown": row["score_breakdown"],
            "confidence": float(row["confidence"]) if row["confidence"] else None,
        }
        features.append({
            "type": "Feature",
            "geometry": row["geometry"],
            "properties": props,
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "total": len(features),
    }


@router.get("/{municipality_id}")
def list_parcels(
    municipality_id: str,
    tier: Optional[int] = Query(None, ge=1, le=4),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    zoning_code: Optional[str] = None,
    review_status: Optional[str] = None,
    limit: int = Query(3000, le=10000),
    offset: int = 0,
    geojson: bool = True,
    db: Session = Depends(get_db),
):
    """
    Return parcels for a municipality as GeoJSON FeatureCollection (default)
    or as a plain JSON list. Supports filtering by tier, score, zoning, review status.
    """
    # Get latest scan run scores
    score_subq = (
        db.query(ParcelScore)
        .filter(ParcelScore.municipality_id == municipality_id)
        .order_by(ParcelScore.created_at.desc())
        .subquery()
    )

    query = (
        db.query(Parcel, ParcelGeometry, ParcelScore)
        .join(
            ParcelGeometry,
            and_(
                ParcelGeometry.parcel_id == Parcel.parcel_id,
                ParcelGeometry.municipality_id == Parcel.municipality_id,
            ),
        )
        .outerjoin(
            ParcelScore,
            and_(
                ParcelScore.parcel_id == Parcel.parcel_id,
                ParcelScore.municipality_id == Parcel.municipality_id,
            ),
        )
        .filter(Parcel.municipality_id == municipality_id)
    )

    if tier is not None:
        query = query.filter(ParcelScore.tier == tier)
    if min_score is not None:
        query = query.filter(ParcelScore.score >= min_score)
    if zoning_code:
        query = query.filter(Parcel.zoning_code == zoning_code)

    # When no tier filter applied, load a representative sample across all tiers
    # so the map shows green/yellow/orange/red rather than just top scorers.
    # When a tier filter is active, sort by score descending within that tier.
    if tier is not None or min_score is not None:
        results = query.order_by(ParcelScore.score.desc().nullslast()).offset(offset).limit(limit).all()
    else:
        # Round-robin across tiers: fetch top N per tier then combine
        from sqlalchemy import func
        tier_limit = limit // 4
        all_results = []
        for t in [1, 2, 3, 4]:
            tier_results = (
                query.filter(ParcelScore.tier == t)
                .order_by(ParcelScore.score.desc().nullslast())
                .limit(tier_limit)
                .all()
            )
            all_results.extend(tier_results)
        # Also grab unscored parcels up to remaining budget
        unscored = (
            query.filter(ParcelScore.score == None)
            .limit(max(0, limit - len(all_results)))
            .all()
        )
        all_results.extend(unscored)
        results = all_results

    if not geojson:
        return [_parcel_to_dict(p, g, s) for p, g, s in results]

    features = []
    for parcel, geom, score in results:
        if geom is None:
            continue
        geom_json = db.execute(ST_AsGeoJSON(geom.geom)).scalar()
        feature = {
            "type": "Feature",
            "geometry": json.loads(geom_json),
            "properties": _parcel_to_dict(parcel, geom, score),
        }
        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features,
        "total": len(features),
    }


@router.post("/search")
def search_parcel(
    data: dict = Body(...),
    db: Session = Depends(get_db),
):
    """
    Search for a specific parcel by address.
    Body: {"address": "14 Main St, Southampton NY 11963"}

    1. Geocodes address to find municipality
    2. Checks if municipality is scanned
    3. Returns closest parcel match by address text or coordinates
    """
    from app.agents.municipality_resolver import resolve_municipality
    from geoalchemy2.functions import ST_AsGeoJSON, ST_Distance, ST_SetSRID, ST_MakePoint
    from sqlalchemy import func, cast, Float

    address = data.get("address", "").strip()
    if not address:
        raise HTTPException(status_code=400, detail="'address' field is required")

    # Step 1: Resolve to municipality
    resolved = resolve_municipality(address)
    if not resolved:
        raise HTTPException(
            status_code=422,
            detail=f"Could not resolve '{address}' to a US municipality."
        )

    municipality_id = resolved["municipality_id"]
    municipality_name = resolved["municipality_name"]
    state = resolved["state"]
    lat = resolved.get("lat")
    lon = resolved.get("lon")

    # Step 2: Check if scanned
    from app.models.municipality import Municipality
    muni = db.query(Municipality).filter(
        Municipality.municipality_id == municipality_id
    ).first()

    parcel_count = db.query(func.count(Parcel.id)).filter(
        Parcel.municipality_id == municipality_id
    ).scalar() or 0

    if not muni or parcel_count == 0:
        return {
            "status": "not_scanned",
            "municipality_id": municipality_id,
            "municipality_name": municipality_name,
            "state": state,
            "message": f"Municipality '{municipality_name}, {state}' not yet scanned. Trigger a scan first.",
        }

    # Step 3: Find closest parcel
    # Try address text match first
    address_words = address.split(",")[0].strip().upper()
    parcel = db.query(Parcel).filter(
        Parcel.municipality_id == municipality_id,
        func.upper(Parcel.address).contains(address_words),
    ).first()

    # Fallback: proximity search using geocoded coordinates
    if not parcel and lat and lon:
        closest = (
            db.query(Parcel, ParcelGeometry)
            .join(
                ParcelGeometry,
                and_(
                    ParcelGeometry.parcel_id == Parcel.parcel_id,
                    ParcelGeometry.municipality_id == Parcel.municipality_id,
                ),
            )
            .filter(Parcel.municipality_id == municipality_id)
            .order_by(
                ST_Distance(
                    ParcelGeometry.geom,
                    func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326),
                )
            )
            .limit(1)
            .first()
        )
        if closest:
            parcel = closest[0]

    if not parcel:
        return {
            "status": "no_match",
            "municipality_id": municipality_id,
            "municipality_name": municipality_name,
            "state": state,
            "message": f"Municipality scanned but no parcel matched '{address}'. Browse the map to find it.",
        }

    # Step 4: Return full parcel detail
    score = db.query(ParcelScore).filter(
        ParcelScore.municipality_id == municipality_id,
        ParcelScore.parcel_id == parcel.parcel_id,
    ).order_by(ParcelScore.created_at.desc()).first()

    geom = db.query(ParcelGeometry).filter(
        ParcelGeometry.municipality_id == municipality_id,
        ParcelGeometry.parcel_id == parcel.parcel_id,
    ).first()

    geom_json = None
    if geom:
        geom_json = json.loads(db.execute(ST_AsGeoJSON(geom.geom)).scalar())

    rules = db.query(ParcelRuleResult).filter(
        ParcelRuleResult.municipality_id == municipality_id,
        ParcelRuleResult.parcel_id == parcel.parcel_id,
    ).order_by(ParcelRuleResult.created_at.desc()).all()

    return {
        "status": "found",
        "municipality_id": municipality_id,
        "municipality_name": municipality_name,
        "state": state,
        "parcel": _parcel_to_dict(parcel, geom, score),
        "geometry": geom_json,
        "rule_results": [
            {
                "rule_id": r.rule_id,
                "rule_category": r.rule_category,
                "result": r.result,
                "explanation": r.explanation,
                "confidence": float(r.confidence) if r.confidence else None,
            }
            for r in rules
        ],
    }


@router.get("/detail/{municipality_id}/{parcel_id}")
def get_parcel(municipality_id: str, parcel_id: str, db: Session = Depends(get_db)):
    parcel = db.query(Parcel).filter(
        Parcel.municipality_id == municipality_id,
        Parcel.parcel_id == parcel_id,
    ).first()
    if not parcel:
        raise HTTPException(status_code=404, detail="Parcel not found")

    score = db.query(ParcelScore).filter(
        ParcelScore.municipality_id == municipality_id,
        ParcelScore.parcel_id == parcel_id,
    ).order_by(ParcelScore.created_at.desc()).first()

    rules = db.query(ParcelRuleResult).filter(
        ParcelRuleResult.municipality_id == municipality_id,
        ParcelRuleResult.parcel_id == parcel_id,
    ).order_by(ParcelRuleResult.created_at.desc()).all()

    analyst = db.query(ParcelAnalystRecord).filter(
        ParcelAnalystRecord.municipality_id == municipality_id,
        ParcelAnalystRecord.parcel_id == parcel_id,
    ).first()

    return {
        "parcel": _parcel_to_dict(parcel, None, score),
        "rule_results": [
            {
                "rule_id": r.rule_id,
                "rule_category": r.rule_category,
                "result": r.result,
                "explanation": r.explanation,
                "assumptions_used": r.assumptions_used,
                "confidence": float(r.confidence) if r.confidence else None,
            }
            for r in rules
        ],
        "analyst": _analyst_to_dict(analyst) if analyst else None,
    }


@router.get("/zoning-envelope/{municipality_id}/{parcel_id}")
def get_zoning_envelope(municipality_id: str, parcel_id: str, db: Session = Depends(get_db)):
    """Resolve a parcel's zoning envelope (setbacks, height, coverage, FAR).

    Used by the "Send to Rhino" pipeline to embed real zoning constraints in
    the DXF metadata so the Grasshopper massing model uses actual setbacks
    instead of arbitrary slider defaults. Returns nulls for any field the
    config doesn't pin down -- the consumer is expected to handle missing
    values (e.g. fall back to slider defaults).
    """
    parcel = db.query(Parcel).filter(
        Parcel.municipality_id == municipality_id,
        Parcel.parcel_id == parcel_id,
    ).first()
    if not parcel:
        raise HTTPException(status_code=404, detail="Parcel not found")

    cfg_row = db.query(MunicipalityConfig).filter(
        MunicipalityConfig.municipality_id == municipality_id,
        MunicipalityConfig.active == True,  # noqa: E712 -- SQLAlchemy expects ==, not is
    ).first()
    config = cfg_row.config_data if cfg_row else {}

    district_key = normalize_zoning_code(parcel.zoning_code, config)
    district = (config.get("zoning_districts") or {}).get(district_key) if district_key else None

    setbacks = (district or {}).get("setbacks") or {}
    return {
        "municipality_id": municipality_id,
        "parcel_id": parcel_id,
        "zoning_code_raw": parcel.zoning_code,
        "district_key": district_key,
        "district_label": (district or {}).get("label"),
        "front_setback_ft": setbacks.get("front_ft"),
        "side_setback_ft": setbacks.get("side_ft"),
        "rear_setback_ft": setbacks.get("rear_ft"),
        "max_height_ft": (district or {}).get("max_height_ft"),
        "max_lot_coverage": (district or {}).get("max_lot_coverage_pct"),
        "max_far": (district or {}).get("far"),
        "min_lot_area_sqft": (district or {}).get("min_lot_area_sqft"),
        "lot_area_sqft": float(parcel.lot_area_sqft) if parcel.lot_area_sqft else None,
        "config_version": cfg_row.version if cfg_row else None,
        "config_confidence": (district or {}).get("confidence"),
    }


@router.put("/analyst/{municipality_id}/{parcel_id}")
def update_analyst_record(
    municipality_id: str,
    parcel_id: str,
    data: dict = Body(...),
    db: Session = Depends(get_db),
):
    record = db.query(ParcelAnalystRecord).filter(
        ParcelAnalystRecord.municipality_id == municipality_id,
        ParcelAnalystRecord.parcel_id == parcel_id,
    ).first()

    if not record:
        record = ParcelAnalystRecord(
            municipality_id=municipality_id,
            parcel_id=parcel_id,
        )
        db.add(record)

    for field in ["notes", "review_status", "outreach_status", "next_step",
                  "confidence_override", "rule_overrides", "flagged", "analyst"]:
        if field in data:
            setattr(record, field, data[field])

    db.commit()
    return {"status": "updated", "parcel_id": parcel_id}


@router.get("/export/{municipality_id}/csv")
def export_parcels_csv(
    municipality_id: str,
    tier: Optional[int] = Query(None),
    min_score: Optional[float] = Query(None),
    db: Session = Depends(get_db),
):
    """Export filtered parcels as CSV."""
    results = list_parcels(
        municipality_id=municipality_id,
        tier=tier,
        min_score=min_score,
        geojson=False,
        limit=2000,
        offset=0,
        db=db,
    )

    output = io.StringIO()
    if not results:
        output.write("No parcels found.\n")
    else:
        writer = csv.DictWriter(output, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={municipality_id}_parcels.csv"},
    )


def _parcel_to_dict(parcel: Parcel, geom, score: Optional[ParcelScore]) -> dict:
    return {
        "parcel_id": parcel.parcel_id,
        "municipality_id": parcel.municipality_id,
        "address": parcel.address,
        "owner_name": parcel.owner_name,
        "zoning_code": parcel.zoning_code,
        "lot_area_sqft": float(parcel.lot_area_sqft) if parcel.lot_area_sqft else None,
        "land_use_type": parcel.land_use_type,
        "assessed_use": parcel.assessed_use,
        "existing_structure_count": parcel.existing_structure_count,
        "score": float(score.score) if score and score.score else None,
        "tier": score.tier if score else None,
        "confidence": float(score.confidence) if score and score.confidence else None,
        "score_breakdown": score.score_breakdown if score else None,
        "blockers": score.blockers if score else None,
        "template_fits": score.template_fits if score else None,
    }


def _analyst_to_dict(record: ParcelAnalystRecord) -> dict:
    return {
        "review_status": record.review_status,
        "outreach_status": record.outreach_status,
        "next_step": record.next_step,
        "notes": record.notes,
        "flagged": record.flagged,
        "analyst": record.analyst,
        "confidence_override": float(record.confidence_override) if record.confidence_override else None,
        "rule_overrides": record.rule_overrides,
    }
