"""
Fetch MassGIS assessor attribute data from the MassGIS ArcGIS Feature Service.

MassGIS FY25+ per-municipality shapefile zips no longer include the Assess layer.
This script fetches the combined TaxPar+Assess attributes directly from MassGIS's
public ArcGIS Feature Service, filtered by TOWN_ID.

Output: data/assessor_{municipality_id}.csv  (joined to TaxPar on LOC_ID)

Usage:
    python scripts/fetch_assessor.py --municipality ma_acton
    python scripts/fetch_assessor.py --town-id 2 --out data/assessor_ma_acton.csv

MassGIS Feature Service:
    https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/L3_TAXPAR_POLY_ASSESS_gdb2/FeatureServer/0
"""

import sys
import os
import argparse
import json
import csv
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# MassGIS Feature Service
# ---------------------------------------------------------------------------

SERVICE_URL = (
    "https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services"
    "/L3_TAXPAR_POLY_ASSESS_gdb2/FeatureServer/0/query"
)

# Fields to fetch — these are the assessor attributes we care about
FIELDS = ",".join([
    "LOC_ID",
    "PROP_ID",
    "SITE_ADDR",
    "ADDR_NUM",
    "FULL_STR",
    "CITY",
    "ZIP",
    "OWNER1",
    "OWN_ADDR",
    "OWN_CITY",
    "OWN_STATE",
    "OWN_ZIP",
    "ZONING",
    "LOT_SIZE",
    "LOT_UNITS",
    "USE_CODE",
    "BLD_AREA",
    "RES_AREA",
    "UNITS",
    "YEAR_BUILT",
    "STYLE",
    "NUM_ROOMS",
    "STORIES",
    "BLDG_VAL",
    "LAND_VAL",
    "TOTAL_VAL",
    "FY",
])

# TOWN_ID lookup for common municipalities
# Run diagnose.py or check your TaxPar data to find yours (TOWN_ID column)
MUNICIPALITY_TOWN_IDS = {
    "ma_acton": 2,
    "ma_ayer": 22,
    "ma_bedford": 32,
    "ma_boxborough": 55,
    "ma_concord": 121,
    "ma_groton": 212,
    "ma_hudson": 248,
    "ma_littleton": 290,
    "ma_maynard": 313,
    "ma_stow": 490,
    "ma_westford": 556,
    # Add more as needed — TOWN_ID is in your TaxPar shapefile TOWN_ID column
}


def fetch_page(town_id: int, offset: int, page_size: int = 2000) -> dict:
    """Fetch one page of records from the MassGIS Feature Service."""
    params = urlencode({
        "where": f"TOWN_ID={town_id} AND POLY_TYPE='FEE'",
        "outFields": FIELDS,
        "returnGeometry": "false",
        "resultOffset": offset,
        "resultRecordCount": page_size,
        "f": "json",
    })
    url = f"{SERVICE_URL}?{params}"
    req = Request(url, headers={"User-Agent": "PlinthSIP/1.0"})
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching assessor data: {e.reason}")
    except URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")


def fetch_all(town_id: int) -> list[dict]:
    """Fetch all assessor records for a town, paginating automatically."""
    records = []
    offset = 0
    page_size = 2000
    page_num = 1

    while True:
        print(f"    Fetching page {page_num} (offset {offset})...")
        data = fetch_page(town_id, offset, page_size)

        if "error" in data:
            raise RuntimeError(f"API error: {data['error']}")

        features = data.get("features", [])
        if not features:
            break

        for f in features:
            attrs = f.get("attributes", {})
            records.append(attrs)

        print(f"    Got {len(features)} records (total so far: {len(records)})")

        # Check if there are more records
        exceeded = data.get("exceededTransferLimit", False)
        if not exceeded or len(features) < page_size:
            break

        offset += page_size
        page_num += 1
        time.sleep(0.3)  # Be polite to the API

    return records


def save_csv(records: list[dict], out_path: Path):
    """Save fetched records to CSV."""
    if not records:
        print("  WARNING: No records to save.")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Get all unique field names
    all_fields = []
    seen = set()
    for r in records:
        for k in r.keys():
            if k not in seen:
                all_fields.append(k)
                seen.add(k)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            # Replace None with empty string for clean CSV
            clean = {k: ("" if v is None else v) for k, v in record.items()}
            writer.writerow(clean)

    print(f"  Saved {len(records)} records to: {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch MassGIS assessor data from ArcGIS Feature Service"
    )
    parser.add_argument(
        "--municipality", default="ma_acton",
        help="Municipality ID (e.g. ma_acton)"
    )
    parser.add_argument(
        "--town-id", type=int, default=None,
        help="Override TOWN_ID (check your TaxPar shapefile if unsure)"
    )
    parser.add_argument(
        "--out", default=None,
        help="Output CSV path (default: data/assessor_{municipality}.csv)"
    )
    args = parser.parse_args()

    # Resolve town ID
    town_id = args.town_id
    if town_id is None:
        town_id = MUNICIPALITY_TOWN_IDS.get(args.municipality)
        if town_id is None:
            print(f"ERROR: Unknown municipality '{args.municipality}'.")
            print(f"Known municipalities: {list(MUNICIPALITY_TOWN_IDS.keys())}")
            print("Or pass --town-id directly (check TOWN_ID column in your TaxPar shapefile).")
            sys.exit(1)

    # Resolve output path
    script_dir = Path(__file__).parent
    backend_dir = script_dir.parent
    data_dir = backend_dir.parent / "data"

    out_path = Path(args.out) if args.out else data_dir / f"assessor_{args.municipality}.csv"

    print(f"Plinth SIP — Fetch Assessor Data")
    print(f"Municipality: {args.municipality}")
    print(f"TOWN_ID:      {town_id}")
    print(f"Output:       {out_path}")
    print(f"Source:       MassGIS ArcGIS Feature Service")
    print()

    print("  Fetching records...")
    try:
        records = fetch_all(town_id)
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        print("\nIf you can't reach the API, check your internet connection.")
        print("You can also download the statewide GDB from MassGIS and extract the Assess table.")
        sys.exit(1)

    if not records:
        print("\nERROR: No records returned. Check TOWN_ID is correct.")
        sys.exit(1)

    print(f"\n  Total records fetched: {len(records)}")

    # Show sample
    if records:
        sample = records[0]
        print("\n  Sample record:")
        for k, v in sample.items():
            if v not in (None, "", "null"):
                print(f"    {k}: {v}")

    save_csv(records, out_path)
    print(f"\n  Done. Now run reingest.bat to load with assessor data.")


if __name__ == "__main__":
    main()
