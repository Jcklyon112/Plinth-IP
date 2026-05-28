# Plinth ADU Rent Calculator

Address-in, ADU rent revenue out. A single-page tool for homeowners and analysts to estimate the rental income of a studio / 1BR / 2BR accessory dwelling unit at any U.S. address, with an optional financial pro forma (cash flow, IRR, sensitivity).

Data sources:
- **RentCast** (primary): live MLS comps, rent AVM with 5% ADU premium
- **HUD Fair Market Rent** (fallback): county-level government data for rural addresses RentCast can't cover

Built for the Northeast US first, but works anywhere either source has data.

---

## Prerequisites

- Python 3.10+
- Node.js 18+

No Docker, no Postgres — uses SQLite for response caching.

---

## Setup

### Backend

```bash
cd plinth-sip/backend
python -m venv venv

# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
cp ../.env.example .env
```

Fill in `.env`:
- `RENTCAST_API_KEY` — free signup at https://app.rentcast.io
- `HUD_API_TOKEN` — free signup at https://www.huduser.gov/portal/dataset/fmr-api.html

Start the API:

```bash
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### Frontend

```bash
cd plinth-sip/frontend
npm install
npm run dev
```

App: http://localhost:3001

---

## How it works

1. User enters an address.
2. Backend tries RentCast (3 specs: studio / 1BR / 2BR).
3. If RentCast returns no usable data (typical in rural NE), it falls back to HUD FMR:
   - Census geocoder resolves address → county FIPS (free, no auth)
   - HUD FMR API returns county-level rent by bedroom count
   - We apply a 10% modern-construction uplift (FMR represents 40th-percentile standard stock)
4. SQLite caches the response for 7 days keyed on the normalized address.
5. Frontend shows three rent cards (studio/1BR/2BR) and a CTA to open the full pro forma — which includes sources & uses, 10-year cash flows, IRR, sensitivity, and an illustrative JV waterfall.

---

## Project layout

```
plinth-sip/
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   │   ├── rentcast_service.py    # RentCast AVM client
│   │   │   └── hud_fmr_service.py     # Census geocoder + HUD FMR fallback
│   │   ├── models/
│   │   │   └── rent_cache.py          # SQLite cache table
│   │   ├── routers/
│   │   │   └── rent_estimate.py       # POST /rent-estimate
│   │   ├── config.py
│   │   ├── database.py
│   │   └── main.py
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── RentEstimatePage.tsx   # landing page
│       │   └── ProFormaPanel.tsx      # full investment pro forma
│       ├── api/client.ts
│       └── main.tsx
└── .env.example
```
