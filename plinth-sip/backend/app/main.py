import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import municipalities, parcels, templates, scans, exports, analysis, reports, demo, picker, datacenter

app = FastAPI(
    title="Plinth Spatial Intelligence Platform API",
    version="0.1.0",
    description="Internal API for Plinth parcel feasibility analysis.",
)

# Ensure spatial indexes exist on startup
try:
    from app.database import engine
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_parcel_geom_gist ON parcel_geometries USING GIST (geom);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_parcels_muni ON parcels (municipality_id);"))
        conn.commit()
except Exception:
    pass  # DB might not be ready yet

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(municipalities.router, prefix="/municipalities", tags=["Municipalities"])
app.include_router(parcels.router, prefix="/parcels", tags=["Parcels"])
app.include_router(templates.router, prefix="/templates", tags=["Templates"])
app.include_router(scans.router, prefix="/scans", tags=["Scan Runs"])
app.include_router(exports.router, prefix="/exports", tags=["Exports"])
app.include_router(analysis.router, prefix="/analysis", tags=["Analysis"])
app.include_router(reports.router, prefix="/reports", tags=["Reports"])
app.include_router(picker.router, tags=["Picker"])
# Data center feasibility — Phase 2+. The DC analyzer mounts under
# /analysis/datacenter; the bbox-filtered map-layer endpoints mount
# under /grid/*.
app.include_router(datacenter.router, prefix="/analysis", tags=["Data Center"])
app.include_router(datacenter.grid_router, prefix="/grid", tags=["Grid Layers"])


@app.on_event("startup")
async def startup_checks():
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("LangGraph zoning extraction: ENABLED (ANTHROPIC_API_KEY found)")
    else:
        print(
            "WARNING: ANTHROPIC_API_KEY not set. LangGraph zoning extraction is DISABLED.\n"
            "  All parcels will use low-confidence auto-generated configs (16% confidence).\n"
            "  Set ANTHROPIC_API_KEY in backend/.env to enable real zoning data extraction."
        )


@app.get("/health")
def health():
    return {"status": "ok", "service": "plinth-sip-api"}
