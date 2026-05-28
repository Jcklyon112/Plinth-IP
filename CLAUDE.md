# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Plinth ADU Rent Calculator — a focused single-page web app where a homeowner enters an address and gets rental revenue estimates for a studio / 1BR / 2BR accessory dwelling unit (ADU) on their property, with an optional financial pro forma (cash flow, IRR, sensitivity, JV waterfall).

The full app lives in `plinth-sip/`. The originally-mirrored Plinth Feasibility Agent (PFA) at `../Plinth Feasibility Agent (PFA)/` remains intact as the parcel-feasibility platform. This fork has been stripped to ONLY the rent-calculator surface: no parcel ingestion, no zoning rules, no GIS scoring, no Rhino export, no data center analysis.

Target geography: Northeast US first, but works anywhere either data source has coverage.

## Development Commands

### Backend (FastAPI + SQLite)

```bash
cd plinth-sip/backend
# Activate venv (Windows): venv\Scripts\activate
# Activate venv (Unix):    source venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env       # then fill in RENTCAST_API_KEY + HUD_API_TOKEN
uvicorn app.main:app --reload --port 8000
```

API docs at http://localhost:8000/docs. SQLite DB auto-created at `backend/rent_calculator.db` on first request.

### Frontend (React + Vite, no Leaflet)

```bash
cd plinth-sip/frontend
npm install
npm run dev    # http://localhost:3001
npm run build  # tsc && vite build
```

## Architecture

### Single endpoint

`POST /rent-estimate { address, apply_adu_premium }` → returns rent for three ADU specs (studio 450 SF, 1BR 650 SF, 2BR 900 SF) plus `source` ('rentcast' | 'hud_fmr') and `source_note`.

Lookup pipeline (`app/routers/rent_estimate.py`):
1. **Cache check** — SQLite, 7-day TTL, keyed on normalized address.
2. **RentCast** (`app/agents/rentcast_service.py`) — primary; 3 AVM calls (one per spec) with a 5% ADU premium. Returns rent + nearby MLS comparables.
3. **HUD FMR fallback** (`app/agents/hud_fmr_service.py`) — fires when all RentCast specs come back empty (rural areas like NEK VT, Adirondacks, rural ME). Uses the free US Census geocoder to resolve address → county FIPS, then queries the HUD FMR API by entity_id `{state_fips}{county_fips}99999`. Applies a 10% modern-construction uplift (FMR is 40th-percentile standard stock) plus the 5% ADU premium.
4. **Cache write** — store the final response payload.

### Frontend

- `src/main.tsx` mounts `RentEstimatePage` directly (no router).
- `RentEstimatePage.tsx` — address input, three rent cards, comps table, and a "Open Pro Forma →" CTA.
- `ProFormaPanel.tsx` — full investment memo (Sources & Uses, Y1 operating PF, 10-year cash flows, returns KPIs, comp set, JV waterfall, sensitivity grid). Takes `{ address, liveRent, onClose }` props — no parcel/feasibility deps.

### Data Model (SQLite)

One table: `rent_estimate_cache(address_key, source, payload_json, fetched_at)`. Auto-created via `Base.metadata.create_all()` on app startup. No Alembic.

## Key Conventions

- All rent values flow through the `RentEstimate` dataclass in `rentcast_service.py`. HUD service returns the same shape so the router can treat them interchangeably.
- ADU premium (`ADU_PREMIUM = 1.05`) is applied at the source service level when `apply_adu_premium=True`. HUD additionally multiplies by `HUD_MODERN_CONSTRUCTION_UPLIFT = 1.10`.
- The frontend doesn't fetch rent twice: `RentEstimatePage` fetches once on submit, then passes the response into `ProFormaPanel` via props.

## Env Vars

Required:
- `RENTCAST_API_KEY` — without it, the primary path 503s and every request falls through to HUD.
- `HUD_API_TOKEN` — without it, the fallback raises and the router returns 503 with both error details.

Optional:
- `DATABASE_URL` — defaults to `sqlite:///./rent_calculator.db`. Override only if migrating to Postgres later.
