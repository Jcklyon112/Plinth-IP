"""
Shape Analysis Router — Stateless
Fetches parcels from ArcGIS, scores in memory, returns results.
No database writes for parcel data.
"""

import uuid
import json
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app.agents.shape_analyzer import ANALYSIS_JOBS, run_shape_analysis

router = APIRouter()


class AnalyzeShapeRequest(BaseModel):
    municipality_id: str = ""
    geojson_shape: dict
    analysis_id: str = ""


@router.post("/analyze-shape")
def start_shape_analysis_endpoint(
    body: AnalyzeShapeRequest,
    background_tasks: BackgroundTasks,
):
    """
    Start stateless polygon analysis.
    Fetches parcels from ArcGIS, scores in memory, no DB writes.
    """
    analysis_id = body.analysis_id or str(uuid.uuid4())

    background_tasks.add_task(
        run_shape_analysis,
        analysis_id=analysis_id,
        polygon_geojson=body.geojson_shape,
        municipality_id=body.municipality_id,
    )

    return {
        "analysis_id": analysis_id,
        "status": "queued",
    }


@router.get("/analyze-shape/{analysis_id}")
def get_shape_analysis(analysis_id: str):
    """Get current state of a shape analysis job."""
    job = ANALYSIS_JOBS.get(analysis_id)
    if not job:
        return {"status": "not_found", "error": "Analysis job not found"}

    return {
        "analysis_id": analysis_id,
        "status": job.get("status", "unknown"),
        "progress": job.get("progress", 0),
        "parcel_count": job.get("parcel_count", 0),
        "tier_counts": job.get("tier_counts", {}),
        "parcels": job.get("parcels", []),
        "geojson_features": job.get("geojson_features", []),
        "explanations": job.get("explanations", {}),
        "summary": job.get("summary", ""),
        "config_upgrade_notes": job.get("config_upgrade_notes", {}),
        "municipality_name": job.get("municipality_name", ""),
        "state": job.get("state", ""),
        "error": job.get("error", ""),
    }
