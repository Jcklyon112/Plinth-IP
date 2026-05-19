from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter()

PARCELS = {
    "parcel-001": {
        "name": "10 Lauras Lane",
        "coordinates": [
            [-72.3125, 40.9876], [-72.3118, 40.9876],
            [-72.3118, 40.9869], [-72.3125, 40.9869],
            [-72.3125, 40.9876],
        ],
        "crs": "geographic",
    },
    "parcel-002": {
        "name": "Sag Harbor Plot B",
        "coordinates": [
            [-72.3200, 40.9900], [-72.3190, 40.9900],
            [-72.3190, 40.9890], [-72.3200, 40.9890],
            [-72.3200, 40.9900],
        ],
        "crs": "geographic",
    },
    "parcel-003": {
        "name": "East Hampton Lot C",
        "coordinates": [
            [-72.3050, 40.9800], [-72.3040, 40.9800],
            [-72.3040, 40.9788], [-72.3050, 40.9788],
            [-72.3050, 40.9800],
        ],
        "crs": "geographic",
    },
}

PICKER_HTML_PATH = Path(__file__).resolve().parents[2] / "picker.html"


@router.get("/parcels")
def list_demo_parcels():
    return [{"id": k, "name": v["name"]} for k, v in PARCELS.items()]


@router.get("/parcels/{parcel_id}/massing")
def get_demo_massing(parcel_id: str):
    if parcel_id not in PARCELS:
        raise HTTPException(status_code=404, detail="Parcel not found")
    return PARCELS[parcel_id]


@router.get("/picker", response_class=HTMLResponse)
def picker():
    return PICKER_HTML_PATH.read_text(encoding="utf-8")
