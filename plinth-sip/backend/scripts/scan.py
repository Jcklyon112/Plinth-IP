"""
Plinth SIP — Auto-Scan Orchestrator (Phase 5)

One command scans any US municipality from a single address or zip code:

    python scripts/scan.py "14 Main St, Burlington VT"
    python scripts/scan.py "Burlington, VT"
    python scripts/scan.py "05401"
    python scripts/scan.py --municipality vt_burlington  # re-scan existing

Pipeline:
    1. Resolve address/zip → municipality metadata (Census Geocoder)
    2. Look up state in GIS registry → ArcGIS REST endpoint
    3. Fetch all parcel geometries + attributes from ArcGIS REST API
    4. Generate baseline municipality config (if no existing config found)
    5. Register municipality in DB and load config
    6. Ingest parcels into PostGIS
    7. Score all parcels (deterministic rules engine)
    8. Print summary + tier breakdown

Works for any state in the GIS registry. MA, NH, VT, CT, ME, RI supported.
New states: add entry to backend/app/agents/state_gis_registry.py.
"""

import sys
import os
import io
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

# Fix Windows console encoding — force UTF-8 output
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add backend root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.agents.municipality_resolver import resolve_municipality
from app.agents.state_gis_registry import get_state_config, list_supported_states
from app.agents.gis_fetcher import fetch_parcels_as_gdf
from app.agents.auto_config import generate_and_save_config
from app.ingestion.generic_ingest import ingest_from_gdf
from app.config import settings


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------

def _print_header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print('='*60)


def step_resolve(query: str) -> dict:
    """Step 1: Resolve address/zip to municipality metadata."""
    _print_header(f"Step 1 — Resolve: {query}")
    result = resolve_municipality(query)
    if not result:
        print(f"ERROR: Could not resolve '{query}' to a US municipality.")
        print("Try a more specific address: '14 Main St, Burlington VT'")
        sys.exit(1)

    print(f"  Municipality:  {result['municipality_name']}")
    print(f"  State:         {result['state']}")
    print(f"  County:        {result['county']}")
    print(f"  Municipality ID: {result['municipality_id']}")
    print(f"  Location:      {result['lat']:.4f}, {result['lon']:.4f}")
    return result


def step_check_state(state: str) -> dict:
    """Step 2: Verify state is supported in the GIS registry."""
    _print_header(f"Step 2 — GIS Registry Check: {state}")
    state_cfg = get_state_config(state)

    if not state_cfg:
        print(f"ERROR: State '{state}' is not in the GIS registry.")
        print("Supported states:")
        for s in list_supported_states():
            print(f"  {s['state']} — {s['name']} [{s['status']}]")
        sys.exit(1)

    status = state_cfg.get("status", "unknown")
    source = state_cfg.get("parcel_source", "unknown")

    print(f"  State:   {state_cfg['name']}")
    print(f"  Status:  {status}")
    print(f"  Source:  {source}")

    if source not in ("arcgis_rest",):
        print(f"\nERROR: State '{state}' uses '{source}' — not yet supported by the auto-scanner.")
        print("Only 'arcgis_rest' states are currently supported.")
        sys.exit(1)

    if status == "planned":
        print(f"\nWARNING: {state} support is planned but not yet verified.")
        print("The fetch may fail or return incorrect data.")
        print("Proceeding anyway — check results carefully.\n")

    print(f"  Service URL: {state_cfg.get('parcel_service_url', 'N/A')}")
    return state_cfg


def step_fetch_parcels(state_cfg: dict, municipality_name: str):
    """Step 3: Fetch parcel data from ArcGIS REST."""
    _print_header(f"Step 3 — Fetch Parcels: {municipality_name}")
    print(f"  This may take 1-5 minutes depending on municipality size...")

    try:
        gdf = fetch_parcels_as_gdf(state_cfg, municipality_name)
    except RuntimeError as e:
        print(f"\nERROR fetching parcels: {e}")
        print("\nTroubleshooting:")
        print("  1. Check your internet connection")
        print("  2. Verify the municipality name matches GIS data exactly")
        print("  3. Try the state GIS portal manually to confirm the service is available")
        sys.exit(1)

    if len(gdf) == 0:
        print("ERROR: No parcels returned. Municipality may not have data in this GIS service.")
        sys.exit(1)

    print(f"\n  Parcels fetched: {len(gdf)}")
    return gdf


def step_generate_config(
    municipality_id: str,
    municipality_name: str,
    state: str,
    county: str,
    configs_dir: str,
    gdf,
) -> dict:
    """Step 4: Generate baseline municipality config."""
    _print_header(f"Step 4 — Municipality Config: {municipality_name}")

    config = generate_and_save_config(
        municipality_id=municipality_id,
        municipality_name=municipality_name,
        state=state,
        county=county,
        configs_dir=configs_dir,
        gdf=gdf,
    )

    if config.get("auto_generated"):
        print(f"\n  ⚠ Auto-generated config (confidence: {config.get('auto_generated_confidence', 'LOW')})")
        print(f"  Districts created: {list(config.get('zoning_districts', {}).keys())[:10]}")
        print(f"  NOTE: Verify zoning rules against local ordinance before making outreach decisions.")
    else:
        print(f"  Loaded existing config (version {config.get('config_version', '?')})")

    return config


def step_register_municipality(municipality_id: str, municipality_name: str, state: str, county: str):
    """Step 5: Register municipality in DB and load config."""
    _print_header(f"Step 5 — Database Setup: {municipality_id}")

    # Use requests or direct DB calls
    # We use direct DB + API calls here since this is a script
    import requests
    import time

    base_url = "http://localhost:8000"

    # Check if backend is running
    try:
        resp = requests.get(f"{base_url}/municipalities/", timeout=5)
        resp.raise_for_status()
    except Exception:
        print("ERROR: Backend server is not running.")
        print("Start it first: cd plinth-sip/backend && uvicorn app.main:app --reload --port 8000")
        sys.exit(1)

    # Check if municipality already exists
    try:
        resp = requests.get(f"{base_url}/municipalities/{municipality_id}", timeout=5)
        if resp.status_code == 200:
            print(f"  Municipality '{municipality_id}' already exists in DB.")
        else:
            # Create it
            resp = requests.post(f"{base_url}/municipalities/", json={
                "municipality_id": municipality_id,
                "name": municipality_name,
                "county": county,
                "state": state,
            }, timeout=10)
            if resp.status_code in (200, 201):
                print(f"  Created municipality: {municipality_id}")
            elif resp.status_code == 409:
                print(f"  Municipality already exists (409).")
            else:
                print(f"  Warning: municipality creation returned {resp.status_code}")
    except Exception as e:
        print(f"  Warning: {e}")

    # Load config from file
    try:
        resp = requests.post(
            f"{base_url}/municipalities/load-from-file/{municipality_id}",
            timeout=10,
        )
        if resp.status_code == 200:
            result = resp.json()
            print(f"  Config loaded: version {result.get('config_version')}")
        else:
            print(f"  Warning: config load returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"  Warning loading config: {e}")


def step_ingest(gdf, state_cfg: dict, municipality_id: str) -> dict:
    """Step 6: Ingest parcels into PostGIS."""
    _print_header(f"Step 6 — Ingest Parcels: {municipality_id}")
    print(f"  Loading {len(gdf)} parcels into database...")

    result = ingest_from_gdf(gdf, state_cfg, municipality_id)
    return result


def step_score(municipality_id: str):
    """Step 7: Score all parcels."""
    _print_header(f"Step 7 — Score Parcels: {municipality_id}")
    print(f"  Running rules engine and scoring...")

    import requests
    resp = requests.post(f"http://localhost:8000/scans/{municipality_id}/rescore", timeout=30)
    if resp.status_code == 200:
        result = resp.json()
        scan_run_id = result.get("scan_run_id")
        print(f"  Scoring started: scan_run_id={scan_run_id}")
        print(f"  Waiting for scoring to complete...")

        # Poll scan status
        for i in range(120):  # wait up to 2 minutes
            import time
            time.sleep(2)
            status_resp = requests.get(f"http://localhost:8000/scans/detail/{scan_run_id}", timeout=10)
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                status = status_data.get("status")
                scored = status_data.get("parcels_scored", 0)
                if status == "complete":
                    print(f"  ✓ Scoring complete: {scored} parcels scored")
                    return status_data
                elif status == "failed":
                    print(f"  ✗ Scoring failed: {status_data.get('error_log', 'unknown error')}")
                    return status_data
                else:
                    if (i + 1) % 5 == 0:
                        print(f"    ...scoring in progress ({scored} done, {i*2}s elapsed)")
        print("  Scoring is taking longer than expected. Check backend logs.")
    else:
        print(f"  ERROR: Rescore request failed: {resp.status_code}")

    return {}


def step_summary(municipality_id: str, municipality_name: str, state: str):
    """Step 8: Print summary."""
    _print_header(f"Scan Complete: {municipality_name}, {state}")

    import requests
    try:
        # Get tier breakdown from latest scores
        resp = requests.get(
            f"http://localhost:8000/parcels/{municipality_id}",
            params={"limit": 1, "geojson": "false"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            total = data.get("total", 0)
            print(f"  Total parcels on map: {total}")

        # Get tier counts from scans
        resp2 = requests.get(f"http://localhost:8000/scans/{municipality_id}", timeout=10)
        if resp2.status_code == 200:
            scans = resp2.json()
            if scans:
                latest = scans[0]
                print(f"  Last scan: {latest.get('completed_at', 'in progress')}")
                print(f"  Parcels scored: {latest.get('parcels_scored', 0)}")

    except Exception:
        pass

    print(f"\n  ✓ Open the map to see results:")
    print(f"    http://localhost:3000")
    print(f"\n  Municipality: {municipality_id}")
    print(f"  Filter by tier 1 (green) to see top outreach candidates.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Plinth SIP Auto-Scanner — scan any US municipality from one command",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/scan.py "Burlington, VT"
  python scripts/scan.py "14 Main St, Burlington VT"
  python scripts/scan.py "05401"
  python scripts/scan.py "Acton, MA"
  python scripts/scan.py --municipality vt_burlington  (re-scan existing)

Supported states: MA (production), NH/VT/CT/ME/RI (beta)
        """
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="US address, municipality name+state, or zip code",
    )
    parser.add_argument(
        "--municipality",
        default=None,
        help="Re-scan an existing municipality by ID (skips geocoding)",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip fetch step (use existing parcel data in DB)",
    )
    parser.add_argument(
        "--skip-score",
        action="store_true",
        help="Skip scoring step",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and preview data without writing to DB",
    )
    args = parser.parse_args()

    if not args.query and not args.municipality:
        parser.print_help()
        sys.exit(1)

    print(f"\nPlinth SIP — Auto-Scan (Phase 5)")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    configs_dir = os.environ.get("CONFIGS_DIR", str(Path(__file__).parent.parent.parent / "configs"))

    # Step 1: Resolve municipality
    if args.municipality:
        # Re-scan existing — need to load from DB or require state arg
        municipality_id = args.municipality
        print(f"\nRe-scanning existing municipality: {municipality_id}")
        # Parse state from ID prefix (e.g. "vt_burlington" → "VT")
        parts = municipality_id.split("_")
        state = parts[0].upper() if parts else "MA"
        municipality_name = " ".join(parts[1:]).replace("_", " ").title() if len(parts) > 1 else municipality_id
        county = ""
        resolved = {
            "municipality_id": municipality_id,
            "municipality_name": municipality_name,
            "state": state,
            "county": county,
        }
    else:
        resolved = step_resolve(args.query)
        municipality_id = resolved["municipality_id"]
        state = resolved["state"]
        municipality_name = resolved["municipality_name"]
        county = resolved["county"]

    # Step 2: Check state registry
    state_cfg = step_check_state(state)

    if args.skip_fetch:
        print(f"\nSkipping fetch (--skip-fetch). Using existing parcel data.")
        gdf = None
    else:
        # Step 3: Fetch parcels
        gdf = step_fetch_parcels(state_cfg, municipality_name)

    # Step 4: Generate config
    step_generate_config(municipality_id, municipality_name, state, county, configs_dir, gdf)

    if args.dry_run:
        print("\nDRY RUN complete. No data written to database.")
        if gdf is not None:
            print(f"Would ingest {len(gdf)} parcels for {municipality_id}")
        sys.exit(0)

    # Step 5: Register municipality in DB
    step_register_municipality(municipality_id, municipality_name, state, county)

    # Step 6: Ingest
    if gdf is not None:
        ingest_result = step_ingest(gdf, state_cfg, municipality_id)
    else:
        print("\nSkipped ingest (no parcel data fetched).")

    # Step 7: Score
    if not args.skip_score:
        step_score(municipality_id)

    # Step 8: Summary
    step_summary(municipality_id, municipality_name, state)

    print(f"\nFinished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
