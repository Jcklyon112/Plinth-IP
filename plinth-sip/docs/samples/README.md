# Sample DC feasibility reports

Three illustrative parcel reports across the grade spectrum, used in
the PR description and as a fallback reference when source data hasn't
been loaded yet.

| File | Grade | Profile |
|---|---|---|
| `dc-sample-strong.json` | **A** | 62 ac industrial parcel in Loudoun County VA, 230 kV substation 0.4 mi away, dual-feed feasible, in PJM, low rate tier |
| `dc-sample-mid.json` | **C** | 11.6 ac commercial parcel in Boone County IN, 161 kV substation 3.5 mi away, no dual feed, in MISO, medium rate tier |
| `dc-sample-weak.json` | **F** | 0.4 ac residential parcel in Provincetown MA, only sub-115 kV substation, FEMA Zone VE — F-gated on both acreage and flood |

## Regenerating

These files are illustrative — the addresses and operator names are
chosen to typify each tier, not to represent real parcels in the DB.
Once a real parcel + grid data is loaded, regenerate them with:

```powershell
cd plinth-sip\backend
.\venv\Scripts\activate
python scripts\dc_demo.py `
  --parcel <muni>/<parcel_id>=strong `
  --parcel <muni>/<parcel_id>=mid `
  --parcel <muni>/<parcel_id>=weak
```

The script writes back to `docs/samples/dc-sample-<label>.json`.

## What each file demonstrates

### `dc-sample-strong.json` (A)
- Substation proximity score = 100 (≥115 kV within 1 mi)
- Dual-feed = true (two substations on disjoint corridors within 5 mi)
- 230 kV line within 1 mi (greenfield-substation potential)
- Low rate tier (Dominion VA industrial)
- Hyperscale acreage tier
- All four `tierFit` levels populate (edge/colo/hyperscale/campus)
- No gating issues; no F-gates triggered

### `dc-sample-mid.json` (C)
- Substation proximity = 60 (3-5 mi band, ≥115 kV)
- Dual-feed = false (single corridor)
- No 230 kV line within 1 mi
- Medium rate tier (Duke IN industrial)
- Colo acreage tier (5-25 ac)
- Two `tierFit` levels (edge/colo)
- Concerns surfaced in `scoreRationale`

### `dc-sample-weak.json` (F)
- Two F-gates fire simultaneously (sub-acre + Zone VE)
- `nearestTransmissionSubstation` is null — only sub-115 kV in dataset
- Empty `tierFit` array (no DC tier fits)
- `gatingIssues` array populated with the disqualifying conditions
- Very High rate tier (>14 c/kWh)
- All three standard caveats still appear in `warnings`
