"""Picker router — minimal Grasshopper-handoff UI.

Serves a small HTML picker that lists real parcels from the Sag Harbor
GeoJSON cache and exposes their exterior-ring coordinates for Rhino /
Grasshopper consumption. Adds new routes only — does not modify existing
parcel/data-model routes.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter()

REPO_ROOT = Path(__file__).resolve().parents[4]
PICKER_HTML_PATH = REPO_ROOT / "plinth-sip" / "backend" / "picker.html"
REAL_PARCELS_PATH = (
    REPO_ROOT / "plinth-sip" / "data" / "cache"
    / "new_york_sag_harbor_20260404.geojson"
)


def _load_real_parcels() -> dict:
    """Index Sag Harbor parcels by PRINT_KEY, keeping the exterior ring."""
    parcels: dict = {}
    if not REAL_PARCELS_PATH.exists():
        return parcels
    with open(REAL_PARCELS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for feat in data.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Polygon":
            continue
        rings = geom.get("coordinates") or []
        if not rings or not rings[0]:
            continue
        props = feat.get("properties") or {}
        pid = props.get("PRINT_KEY")
        if not pid:
            continue
        addr = (props.get("PARCEL_ADDR") or "").strip()
        owner = (props.get("PRIMARY_OWNER") or "").strip()
        name = addr or owner or pid
        parcels[pid] = {
            "name": name,
            "coordinates": [[float(x), float(y)] for x, y in rings[0]],
            "crs": "geographic",
        }
    return parcels


REAL_PARCELS = _load_real_parcels()


@router.get("/picker", response_class=HTMLResponse)
def picker():
    if not PICKER_HTML_PATH.exists():
        raise HTTPException(status_code=500, detail="picker.html not found")
    return PICKER_HTML_PATH.read_text(encoding="utf-8")


@router.get("/picker/parcels")
def list_picker_parcels():
    return [{"id": pid, "name": p["name"]} for pid, p in REAL_PARCELS.items()]


@router.get("/parcels/{parcel_id}/rhino")
def get_parcel_rhino(parcel_id: str):
    parcel = REAL_PARCELS.get(parcel_id)
    if parcel is None:
        raise HTTPException(status_code=404, detail="Parcel not found")
    return {
        "name": parcel["name"],
        "coordinates": parcel["coordinates"],
        "crs": parcel["crs"],
    }
