# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Plinth Spatial Intelligence Platform (SIP) — an internal GIS platform for parcel feasibility analysis. It evaluates whether residential parcels can support Plinth ADU (accessory dwelling unit) deployments by running zoning/dimensional/physical rules against parcel data, scoring parcels into tiers, and surfacing results via a map UI.

The main application lives in `plinth-sip/`. There is also a root-level `venv/` (Python) that is separate from the backend's own venv at `plinth-sip/backend/venv/`.

## Development Commands

### Database (PostgreSQL + PostGIS via Docker)
```bash
cd plinth-sip
docker compose up db -d
```

### Backend (FastAPI + SQLAlchemy)
```bash
cd plinth-sip/backend
# Activate venv (Windows): venv\Scripts\activate
# Activate venv (Unix): source venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env
alembic upgrade head                     # run migrations
CONFIGS_DIR=../configs python scripts/seed.py  # seed templates + Acton config
uvicorn app.main:app --reload --port 8000
```
- API docs at http://localhost:8000/docs

### Frontend (React + Vite + Leaflet)
```bash
cd plinth-sip/frontend
npm install
npm run dev       # starts on port 3000
npm run build     # tsc && vite build
```

### Key API Operations
```bash
# Load municipality config
curl -X POST http://localhost:8000/municipalities/load-from-file/ma_acton
# Trigger rescoring
curl -X POST http://localhost:8000/scans/ma_acton/rescore
```

## Architecture

### Rules Engine Pipeline

The core workflow: **Ingest parcels -> Evaluate rules -> Score -> Tier assignment**

1. **Ingestion** (`app/ingestion/`): Raw GIS data (e.g., MassGIS shapefiles) is normalized into the universal Plinth parcel schema via municipality-specific adapters (`field_map`, `use_code_map`).

2. **Runner** (`app/engine/runner.py`): Orchestrates evaluation for a single parcel. Handles zoning code normalization (raw GIS codes -> config district keys via `zoning_code_map`) and land use type normalization (4-digit MassGIS codes -> 3-digit internal types).

3. **Rules** (`app/engine/rules/`): 12 rules across 5 categories, each returning a `RuleResult` with `pass|conditional|fail|unknown` plus confidence score:
   - **Dimensional**: `min_lot_size`, `adu_max_size`, `lot_coverage`, `buildable_envelope`
   - **Use**: `use_allowed`, `adu_permitted`
   - **Physical**: `overlay_constraints`, `access_likely`
   - **Septic**: `sewer_available`, `septic_capacity`
   - **Deployment**: `delivery_access`, `existing_structures`

4. **Scoring** (`app/engine/scoring.py`): Weighted composite score across 6 category groups. Hard-block rules (`use_allowed`, `adu_permitted`, `overlay_constraints`) force Tier 4 on fail. Tiers: 1 (Green, >=85), 2 (Yellow, >=65), 3 (Orange, >=40), 4 (Red, <40).

### Municipality Config System

JSON configs in `plinth-sip/configs/municipalities/` define per-municipality zoning districts, overlay constraints, septic assumptions, sewer service, and `zoning_code_map`. Loaded into DB via seed script or API endpoint.

### Data Model (PostgreSQL + PostGIS)

Key tables: `municipalities`, `municipality_configs`, `parcels`, `parcel_geometries` (PostGIS MULTIPOLYGON), `parcel_rule_results`, `parcel_scores`, `parcel_analyst_records`, `scan_runs`, `plinth_templates`, `overlays`, `exports`.

Parcels are keyed by `(parcel_id, municipality_id)`. Scoring results are linked to `scan_runs` for versioning.

### Rescoring Flow

`POST /scans/{municipality_id}/rescore` creates a `ScanRun`, then runs scoring as a FastAPI background task. Each parcel is evaluated against the active config, and rule results + scores are bulk-written to DB (committed every 100 parcels).

### Frontend

React + Leaflet map with parcel visualization, filter bar, and detail panel showing rule results and score breakdown. Communicates with backend via Axios. API base URL configured via `VITE_API_URL`.

## Key Patterns

- Many Phase 1 rules use heuristics (lot-area-based estimates) with explicit confidence scores and notes about Phase 2 replacements (geometry-based calculations).
- `buildable_envelope` rule uses Shapely geometry with setback buffers when geometry is available; falls back to area-based heuristic otherwise.
- Parcel dicts flow through the engine as plain dicts (not ORM objects). The router converts ORM models to dicts before calling `evaluate_parcel()`.
- Database uses `psycopg` (v3) driver, not `psycopg2`. Connection string format: `postgresql+psycopg://`.
