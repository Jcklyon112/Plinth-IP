"""
Reports router — generates and streams downloadable PDF reports for individual parcels.
"""

import json
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from geoalchemy2.functions import ST_AsGeoJSON
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.parcel import Parcel, ParcelScore, ParcelRuleResult, ParcelGeometry
from app.reports.parcel_report import generate_parcel_report

router = APIRouter()


def _fetch_geometry_geojson(db: Session, parcel_id: str, municipality_id: str) -> dict | None:
    geom = (
        db.query(ParcelGeometry)
        .filter(
            ParcelGeometry.parcel_id == parcel_id,
            ParcelGeometry.municipality_id == municipality_id,
        )
        .first()
    )
    if not geom:
        return None
    raw = db.execute(ST_AsGeoJSON(geom.geom)).scalar()
    return json.loads(raw) if raw else None


class ParcelReportRequest(BaseModel):
    """Accepts a full parcel dict from the frontend (same shape as the GeoJSON properties)."""
    parcel_id: str
    municipality_id: str
    address: str | None = None
    owner_name: str | None = None
    zoning_code: str | None = None
    zoning_district_label: str | None = None
    lot_area_sqft: float | None = None
    score: float | None = None
    tier: int | None = None
    confidence: float | None = None
    score_breakdown: dict | None = None
    blockers: list | None = None
    rule_results: list | None = None
    template_fits: list | None = None
    # Allow any extra fields
    model_config = {"extra": "allow"}


@router.post("/parcel-pdf")
async def generate_parcel_pdf(body: ParcelReportRequest, db: Session = Depends(get_db)):
    """
    Accept parcel data from the frontend and return a downloadable PDF report.
    The frontend sends the full parcel properties object (same as map GeoJSON props).
    """
    parcel_dict = body.model_dump(exclude_none=False)

    if not parcel_dict.get("geometry_geojson"):
        geojson = _fetch_geometry_geojson(db, parcel_dict["parcel_id"], parcel_dict["municipality_id"])
        if geojson:
            parcel_dict["geometry_geojson"] = geojson

    try:
        pdf_bytes = generate_parcel_report(parcel_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    parcel_id = parcel_dict.get("parcel_id", "unknown")
    filename = f"plinth_report_{parcel_id}.pdf"

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"",
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@router.get("/parcel/{parcel_id}/pdf")
async def generate_parcel_pdf_by_id(
    parcel_id: str,
    municipality_id: str,
    db: Session = Depends(get_db),
):
    """
    Look up parcel from DB by parcel_id + municipality_id and generate report.
    Useful for server-side generation without needing frontend to send full data.
    """
    # Fetch parcel
    parcel = db.query(Parcel).filter(
        Parcel.parcel_id == parcel_id,
        Parcel.municipality_id == municipality_id,
    ).first()

    if not parcel:
        raise HTTPException(status_code=404, detail=f"Parcel {parcel_id} not found in {municipality_id}")

    # Fetch latest score
    score_row = (
        db.query(ParcelScore)
        .filter(ParcelScore.parcel_id == parcel_id, ParcelScore.municipality_id == municipality_id)
        .order_by(ParcelScore.created_at.desc())
        .first()
    )

    # Fetch rule results (latest scan run)
    rule_rows = (
        db.query(ParcelRuleResult)
        .filter(ParcelRuleResult.parcel_id == parcel_id, ParcelRuleResult.municipality_id == municipality_id)
        .order_by(ParcelRuleResult.created_at.desc())
        .limit(20)
        .all()
    )

    rule_results = [
        {
            "rule_id": r.rule_id,
            "rule_category": r.rule_category,
            "result": r.result,
            "explanation": r.explanation,
            "confidence": r.confidence,
        }
        for r in rule_rows
    ]

    parcel_dict = {
        "parcel_id": parcel.parcel_id,
        "municipality_id": parcel.municipality_id,
        "address": parcel.address,
        "owner_name": parcel.owner_name,
        "zoning_code": parcel.zoning_code,
        "lot_area_sqft": parcel.lot_area_sqft,
        "existing_building_footprint_area": parcel.existing_building_footprint_area,
        "existing_structure_count": parcel.existing_structure_count,
        "score": score_row.score if score_row else None,
        "tier": score_row.tier if score_row else None,
        "confidence": score_row.confidence if score_row else None,
        "score_breakdown": score_row.score_breakdown if score_row else {},
        "blockers": score_row.blockers if score_row else [],
        "rule_results": rule_results,
        "template_fits": score_row.template_fits if score_row else [],
        "geometry_geojson": _fetch_geometry_geojson(db, parcel_id, municipality_id),
    }

    try:
        pdf_bytes = generate_parcel_report(parcel_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    filename = f"plinth_report_{parcel_id}.pdf"

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"",
            "Content-Length": str(len(pdf_bytes)),
        },
    )
