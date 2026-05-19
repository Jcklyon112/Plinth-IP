import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI()

REAL_PARCELS_PATH = (
    Path(__file__).resolve().parent
    / "plinth-sip" / "data" / "cache"
    / "new_york_sag_harbor_20260404.geojson"
)


def _load_real_parcels() -> dict:
    """Load Sag Harbor parcel boundaries from the cached GeoJSON.

    Each feature becomes {parcel_id: {name, coordinates, crs}} where
    parcel_id = PRINT_KEY and coordinates is the exterior ring of the polygon.
    """
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
        if addr:
            name = addr
        elif owner:
            name = owner
        else:
            name = pid
        parcels[pid] = {
            "name": name,
            "coordinates": [[float(x), float(y)] for x, y in rings[0]],
            "crs": "geographic",
        }
    return parcels


REAL_PARCELS = _load_real_parcels()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PARCELS = {
    "parcel-001": {
        "name": "10 Lauras Lane",
        "coordinates": [
            [-72.3125, 40.9876], [-72.3118, 40.9876],
            [-72.3118, 40.9869], [-72.3125, 40.9869],
            [-72.3125, 40.9876]
        ],
        "crs": "geographic"
    },
    "parcel-002": {
        "name": "Sag Harbor Plot B",
        "coordinates": [
            [-72.3200, 40.9900], [-72.3190, 40.9900],
            [-72.3190, 40.9890], [-72.3200, 40.9890],
            [-72.3200, 40.9900]
        ],
        "crs": "geographic"
    },
    "parcel-003": {
        "name": "East Hampton Lot C",
        "coordinates": [
            [-72.3050, 40.9800], [-72.3040, 40.9800],
            [-72.3040, 40.9788], [-72.3050, 40.9788],
            [-72.3050, 40.9800]
        ],
        "crs": "geographic"
    }
}

@app.get("/parcels")
def list_parcels():
    return [{"id": k, "name": v["name"]} for k, v in PARCELS.items()]

@app.get("/parcels/{parcel_id}/massing")
def get_massing(parcel_id: str):
    if parcel_id not in PARCELS:
        raise HTTPException(status_code=404, detail="Parcel not found")
    return PARCELS[parcel_id]

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/parcels/real")
def list_real_parcels():
    return [{"id": pid, "name": p["name"]} for pid, p in REAL_PARCELS.items()]


@app.get("/parcels/{parcel_id}/rhino")
def get_rhino(parcel_id: str):
    parcel = REAL_PARCELS.get(parcel_id)
    if parcel is None:
        raise HTTPException(status_code=404, detail="Parcel not found")
    return {
        "name": parcel["name"],
        "coordinates": parcel["coordinates"],
        "crs": parcel["crs"],
    }


@app.get("/picker", response_class=HTMLResponse)
def picker():
    with open("picker.html") as f:
        return f.read()
