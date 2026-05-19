# Plinth Spatial Intelligence Platform

Internal GIS platform for parcel feasibility analysis, scoring, and outreach targeting.

---

## Prerequisites

- Docker Desktop (running)
- Python 3.10+
- Node.js 18+

---

## First-Time Setup

### Step 1 — Start the database

Open a terminal, navigate to the `plinth-sip` folder, and run:

```bash
docker compose up db -d
```

Wait about 10 seconds for PostgreSQL + PostGIS to initialize.

### Step 2 — Set up the backend

```bash
cd backend
python -m venv venv

# On Windows:
venv\Scripts\activate

# On Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

Copy the environment file:

```bash
cp ../.env.example .env
```

### Step 3 — Run database migrations

```bash
cd backend
alembic upgrade head
```

### Step 4 — Seed the database (templates + Acton config)

```bash
cd backend
CONFIGS_DIR=../configs python scripts/seed.py
```

On Windows, set the variable differently:

```cmd
set CONFIGS_DIR=..\configs
python scripts\seed.py
```

### Step 5 — Start the backend API

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

The API will be live at: http://localhost:8000
API docs (auto-generated): http://localhost:8000/docs

### Step 6 — Set up and start the frontend

Open a second terminal window:

```bash
cd frontend
npm install
npm run dev
```

The map will be live at: http://localhost:3000

---

## Load Municipality Config

After the backend is running, load the Acton config via the API:

```bash
curl -X POST http://localhost:8000/municipalities/load-from-file/ma_acton
```

Or open http://localhost:8000/docs in your browser and use the interactive API explorer.

---

## Run Scoring

Once parcels are loaded (via ingestion scripts — see below), trigger scoring:

```bash
curl -X POST http://localhost:8000/scans/ma_acton/rescore
```

---

## Loading Parcel Data (Phase 1)

Parcel data for Massachusetts towns is available from MassGIS:
https://www.mass.gov/info-details/massgis-data-property-tax-parcels

1. Download the Acton shapefile from MassGIS
2. Place it in `backend/data/raw/ma_acton/`
3. Run the ingestion script (to be built in Phase 1 sprint 2):
   ```bash
   python scripts/ingest.py --municipality ma_acton --file data/raw/ma_acton/parcels.shp
   ```

---

## Project Structure

```
plinth-sip/
├── backend/
│   ├── app/
│   │   ├── engine/          # Rules engine + scoring engine
│   │   │   ├── rules/       # One file per rule category
│   │   │   ├── scoring.py
│   │   │   └── runner.py
│   │   ├── ingestion/       # Normalization + source adapters
│   │   ├── models/          # SQLAlchemy ORM models
│   │   ├── routers/         # FastAPI route handlers
│   │   ├── config.py
│   │   ├── database.py
│   │   └── main.py
│   ├── alembic/             # Database migrations
│   ├── scripts/             # Seed + ingestion scripts
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── components/      # Map, FilterBar, ParcelDetailPanel
│       ├── api/             # API client
│       └── types/           # TypeScript types
├── configs/
│   └── municipalities/
│       └── ma_acton.json    # Acton municipality config
└── docker-compose.yml
```

---

## Phase 1 Status

- [x] Database schema + migrations
- [x] Municipality config system (ma_acton)
- [x] Plinth template schema (Studio 400, 1BR 550)
- [x] Normalization layer + MassGIS adapter
- [x] Rules engine (11 rules across 5 categories)
- [x] Scoring engine (default profile, 4 tiers)
- [x] FastAPI backend (municipalities, parcels, templates, scans, exports)
- [x] React + Leaflet map frontend
- [x] Parcel detail panel with rule results + score breakdown
- [x] CSV export
- [ ] Parcel ingestion script (next)
- [ ] MassGIS data download + first real scan run (next)
