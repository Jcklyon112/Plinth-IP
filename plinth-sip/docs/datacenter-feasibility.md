# Data Center Feasibility — Methodology

This document explains how the data-center feasibility analyzer
(`backend/app/engine/datacenter/`) evaluates a parcel, what its scoring
rubric means, and the limits of what it can and can't tell you.

> **The single biggest caveat:** we do **not** model available substation
> hosting capacity. Even an "A-grade" parcel can be a dead end for
> interconnection if the local substation is full. Always verify with
> the serving utility before underwriting a site.

---

## 1. Pipeline

When the user clicks **Run Data Center Feasibility** on a parcel, the
backend runs `analyze_parcel(session, parcel_id, municipality_id)`.
The analyzer:

1. **Resolves the parcel** — pulls metadata + centroid + WKT geometry from `parcels` + `parcel_geometries`.
2. **Computes a `grid_data_version`** — a 16-char hash of `last_refresh_at` across the consulted layers. Re-clicks against the same data version return the cached `parcel_datacenter_analyses` row in O(1).
3. **Runs each subreport in parallel-friendly order**:
   - `proximity` — substation / transmission-line distances, dual-feed
   - `generation` — nearest baseload + 25-mile fuel mix
   - `power_cost` — utility lookup + EIA 861 industrial rate + tier
   - `infrastructure` — fiber, gas, FEMA flood, USFWS wetlands
   - `land` — acreage tier, zoning compatibility classifier
   - `iso` — Balancing Authority polygon → ISO/RTO bucket
4. **Scores** — `scoring.score_report()` produces a 0–100 composite from six weighted subscores, applies F-gates, maps to A/B/C/D/F.
5. **Caches** the full JSON in `parcel_datacenter_analyses` keyed by parcel + grid_data_version.
6. **Returns** the spec'd JSON. Three standard caveats and any data-availability warnings live in `warnings[]`.

A separate `analyze_shape(session, geojson, label)` runs the same
pipeline against an ad-hoc polygon (no DB persistence, no cache).

## 2. Source layers

| Loader | Table | Geom | Source |
|---|---|---|---|
| `hifld_substations` | `grid_substations` | POINT | HIFLD Electric Substations |
| `hifld_transmission_lines` | `grid_transmission_lines` | MULTILINESTRING | HIFLD Electric Power Transmission Lines |
| `hifld_power_plants` | `grid_power_plants` | POINT | HIFLD Power Plants (joined to EIA Form 860) |
| `hifld_balancing_authorities` | `grid_balancing_authorities` | MULTIPOLYGON | EIA Atlas Balancing Authority Areas (HIFLD Control Areas as substitute) |
| `hifld_service_territories` | `grid_service_territories` | MULTIPOLYGON | HIFLD Electric Retail Service Territories |
| `hifld_gas_pipelines` | `grid_gas_pipelines` | MULTILINESTRING | HIFLD Natural Gas Pipelines |
| `hifld_fiber` | `grid_fiber_routes` | MULTILINESTRING | HIFLD Long-Haul Fiber when reachable, else `data/grid/fiber_carriers/` (KMZ/GeoJSON) |
| `eia_form861_rates` | `eia_industrial_rates` | — | EIA Form 861 (drop annual XLSX in `data/grid/eia/`) |
| `usfws_nwi` (on-demand) | — | n/a | USFWS National Wetlands Inventory (per-parcel ArcGIS REST query) |

All loaders are idempotent (TRUNCATE + INSERT in one transaction) and
update `grid_refresh_metadata`. Run `data/grid/refresh_all.py` to refresh
all layers; run `python -m data.grid.loaders.<name>` to refresh one.

The first six are HIFLD ArcGIS FeatureServer URLs configured in
`data/grid/sources.py` and overridable via `PLINTH_GRID_*_URL` env vars
(HIFLD republishes change the URL suffix periodically).

---

## 3. Definitions

Some terms are precise; others are heuristics. This section makes them
explicit so readers know what's being measured.

- **Transmission-class substation**: `max_voltage_kv >= 115`. Below
  this, substations are distribution-tier and serve loads in the
  hundreds of kW to single-digit MW range — not useful for a DC.
- **Baseload plant**: `primary_fuel ∈ {nuclear, gas}` AND
  `summer_capacity_mw >= 100`. The 100-MW floor filters out peakers
  and small CHP plants. Nuclear and combined-cycle gas are the only
  fleet-grade baseload sources at scale today.
- **Dual-feed feasible**: two distinct ≥115kV substations within 5 mi,
  whose nearby transmission lines are *disjoint* — meaning a single
  contingency on a corridor can't take out both feeds. We approximate
  "nearby line" by `ST_DWithin(line, substation, 0.5 mi)` since HIFLD
  doesn't ship explicit substation→line topology. See `proximity.is_dual_feed_from_line_sets`.
- **Transmission corridor count**: number of distinct `(owner, voltage_class)` pairs in transmission lines within 5 mi. Proxy for redundancy.
- **Acreage tiers**:
  - Edge: 1–5 ac
  - Colo: 5–25 ac
  - Hyperscale-capable: 25–100 ac
  - Campus-capable: 100+ ac
  - Sub-1-acre: F-gated (no DC tier fits).
- **ISO/RTO bucket**: derived from the parcel's containing Balancing
  Authority polygon, then mapped via `data/grid/iso_metadata.json`.
  Possible values: `PJM | MISO | ERCOT | CAISO | NYISO | ISO-NE | SPP | NON-ISO`.
- **Rate tier**: from EIA 861 industrial sector rate (latest year per utility).
  - Low: <6 c/kWh
  - Medium: 6–10 c/kWh
  - High: 10–14 c/kWh
  - Very High: >14 c/kWh

---

## 4. Scoring rubric

Composite is a weighted sum of six subscores, each on 0–100:

| Subscore | Weight | Drivers |
|---|---:|---|
| **Grid** | 40% | Substation proximity (60% of grid), transmission line proximity (25%), dual-feed (15%) |
| **Power cost** | 15% | Rate tier (Low → 100, Very High → 20, unknown → 60) |
| **Infrastructure** | 15% | Fiber dist (40% of infra), gas dist (20%), FEMA flood (20%), wetlands (20%) |
| **Land** | 15% | Acreage (60% of land), zoning compatibility (40%) |
| **Generation** | 10% | Nearest baseload distance + 25-mile capacity bonus |
| **ISO** | 5% | NON-ISO penalized (70 vs 100 in-ISO) |

### Substation proximity (60% of grid)
- ≥115kV within **<1 mi** → 100
- ≥115kV within **1–3 mi** → 80
- ≥115kV within **3–5 mi** → 60
- otherwise (or no ≥115kV in dataset) → ≤35

### Letter grade

| Composite | Grade |
|---:|:---|
| ≥85 | **A** |
| ≥70 | **B** |
| ≥55 | **C** |
| ≥40 | **D** |
| <40 | **F** |

### F-gates (force F regardless of composite)

These overrule the composite because they are independently disqualifying:

1. **No ≥115kV substation within 25 mi.** Greenfield substation
   construction is doable but adds 18–36 mo and tens of millions to a
   project; if there's no transmission anywhere near, this isn't a DC site.
2. **Parcel <1 acre.** Sub-edge; can't even support a small edge
   container deployment with redundant power feeds.
3. **FEMA flood zone V / VE / FLOODWAY.** Coastal high-hazard or
   floodway is a no-build for critical infrastructure.
4. **Wetland coverage >75%.** Buildable area is negligible.

### A separate substation-only grade

For drill-down UI, `scoring.substation_grade(distance_mi, max_voltage_kv)`
returns A/B/C/D against just substation proximity, per the user's
original spec rubric:
- **A**: <1 mi & ≥115kV
- **B**: 1–3 mi & ≥115kV
- **C**: 3–5 mi & ≥115kV
- **D**: >5 mi or only sub-115kV available

---

## 5. The three caveats (surfaced verbatim on every report)

1. *Available substation capacity is NOT modeled — verify with the
   serving utility before proceeding.*
2. *Interconnection queue position is NOT live — see ISO link for
   current status.*
3. *Industrial rates are utility averages from EIA 861 — actual
   large-load tariffs may differ significantly.*

These come from `analyzer.STANDARD_WARNINGS` and are appended to
`warnings[]` ahead of any data-availability messages.

---

## 6. Known limitations

### Data we don't have
- **Substation hosting capacity** — the make-or-break number. Utilities
  publish hosting maps occasionally (PG&E, Eversource, NYSEG); none
  cover the whole country, none are machine-readable. Until utilities
  open this up via a national feed, the analyzer can only flag
  proximity, not feasibility.
- **Live interconnection queue** — ISOs publish queue snapshots; we
  surface the dashboard URL but don't scrape position. Updating
  `iso_metadata.json` is the human-in-the-loop substitute.
- **Real large-load tariffs** — EIA 861 reports utility-wide averages
  by sector. Actual demand-rate schedules for 50+ MW loads are
  bilateral and far from average.
- **Site-level slope / soil / soils stability** — the ADU pipeline has
  some of this; the DC analyzer doesn't yet integrate it. TODO.

### Approximations we do
- **Dual-feed via 0.5 mi line-buffer.** HIFLD doesn't ship
  substation↔line topology, so we can't be certain which lines feed
  which substation. The 0.5 mi heuristic catches most overhead
  connections but misses underground stub feeds.
- **Transmission corridor count via `(owner, voltage_class)`.** A
  single utility running parallel circuits at the same voltage class on
  one ROW counts as 1 corridor; should be 1, but a single utility
  running the same voltage on two ROWs would also count as 1, which
  understates redundancy. The dual-feed signal is a better tie-breaker.
- **ISO/RTO derivation via BA polygons + override JSON.** The bucket
  is right at the regional level; smaller BAs (e.g., utilities inside
  ERCOT) may need refinement.
- **Wetlands via on-demand WFS.** USFWS publishes a national WFS but
  it can be slow. We cache per-parcel inside the analyzer cache so
  re-clicks are free until grid_data_version changes.
- **Fiber as a coarse signal.** HIFLD long-haul fiber is sometimes
  restricted; fall back is the carrier-KMZ drop-zone. Distance to a
  long-haul backbone is a *necessary* but not *sufficient* signal —
  service providers and dark fiber availability vary by carrier.
- **No DEM/slope.** The infra block currently doesn't compute slope.
  TODO: wire in the same DEM the ADU pipeline already touches in some
  states.

---

## 7. Cache + invalidation

- Every analysis result is cached in `parcel_datacenter_analyses` keyed
  by `(parcel_id, municipality_id, grid_data_version)`.
- `grid_data_version` is `sha256("|".join("layer:isoformat(last_refresh_at)"))[:16]`
  across `_LAYERS_USED`. Refreshing **any** of those layers changes the
  hash and invalidates prior rows for the same parcel.
- A fresh DB (no refreshes yet) gets the fixed key `"no-grid-data"` so
  the cache still works deterministically.
- The `/analysis/datacenter` endpoint accepts `use_cache: false` to
  force recompute.

---

## 8. Running it

```powershell
# 1) Apply the migration
cd plinth-sip\backend
.\venv\Scripts\activate
alembic upgrade head

# 2) Refresh public data (network calls, several minutes total)
$env:CONFIGS_DIR = "..\configs"
python ..\data\grid\refresh_all.py

# 3) Drop the latest EIA 861 industrial-rates workbook (annual)
#    into data\grid\eia\, then re-run the EIA loader specifically:
python -m data.grid.loaders.eia_form861_rates

# 4) (Optional) Drop carrier KMZ/GeoJSON files into
#    data\grid\fiber_carriers\ and re-run the fiber loader.

# 5) Start the backend
uvicorn app.main:app --reload --port 8000
```

API: `POST http://localhost:8000/analysis/datacenter` with
`{ "municipality_id": "ma_acton", "parcel_id": "M_192712_899423" }`.

In the frontend, click **DC Mode** in the top-left of the map, click a
parcel, then switch to the **Data Center Analysis** tab in the parcel
panel.

---

## 9. How to update ISO posture

`data/grid/iso_metadata.json` is hand-edited. Each ISO has:

- `queue_dashboard_url` — link the analyzer surfaces verbatim
- `typical_queue_timeline` — rough queue-time string
- `current_posture` — your free-text summary of large-load posture as of last review
- `ba_codes` — list of HIFLD/EIA Balancing Authority codes that map to this ISO

Anything in `current_posture` shows up directly in the report. Update
it whenever a meaningful policy change happens (capacity reform,
queue suspension, etc.) so the surfaced posture matches reality.

---

## 10. Sample runs

See `docs/samples/` for three illustrative parcel reports across the
grade spectrum:
- `dc-sample-strong.json` — Tier-A: 25-acre industrial parcel near a 345 kV
  substation, in PJM, low rate tier
- `dc-sample-mid.json` — Tier-B/C: 8-acre commercial parcel, 161 kV
  substation 3.5 mi away, in MISO
- `dc-sample-weak.json` — Tier-F: 0.5-acre residential parcel in coastal
  flood zone, sub-115 kV substation only

These are produced by `backend/scripts/dc_demo.py`; once data is
loaded, run that script with three real parcel ids to regenerate.
