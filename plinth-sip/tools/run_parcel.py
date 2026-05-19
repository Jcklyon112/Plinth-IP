"""Run the DC analyzer against any parcel - by lat/lon or address.

Usage:
    # Coordinates + acreage (most direct)
    python tools/run_parcel.py --latlon 42.4844 -71.4324 --acres 3.0

    # Address (OSM Nominatim geocode)
    python tools/run_parcel.py --address "17 Independence Rd, Acton MA" --acres 3.0

    # JSON output instead of the formatted readout
    python tools/run_parcel.py --latlon 42.4844 -71.4324 --acres 3.0 --json

This is a thin wrapper around `analyzer._build_report()`. It does NOT
persist anything to the DB and does NOT consult the parcel_datacenter_
analyses cache. The grid lookup hits the actual loaded HIFLD/EIA tables
(run data/grid/refresh_all.py first).

For an "any Acton MA parcel" workflow without a real MassGIS polygon, we
synthesize a square parcel of the requested acreage centered on the
input point. The grid-context lookup uses the centroid only, so the
square is just enough geometry for the wetland-coverage NWI fetch to
have something to overlap; for accurate wetland scoring you'd want the
real parcel polygon (TODO: --massgis-l3 lookup mode).

Run from the repo root with the backend venv active.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

# Make `app.*` importable when invoked as `python tools/run_parcel.py`
# from the repo root, and `data.*` so the analyzer's iso_metadata path
# resolution still works.
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[1]              # plinth-sip/
_BACKEND = _PROJECT_ROOT / "backend"
for p in (_PROJECT_ROOT, _BACKEND):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


# --- inputs ----------------------------------------------------------

def _geocode_address(address: str) -> tuple[float, float]:
    """OSM Nominatim geocode. Returns (lon, lat). Polite delay applied.

    Nominatim's TOS asks for a real User-Agent and 1 req/s max. We're
    making a single call so we just set the UA and don't loop.
    """
    import requests
    headers = {"User-Agent": "PlinthSIP-run_parcel/0.1 (+https://plinth.example)"}
    params = {"q": address, "format": "json", "limit": 1, "addressdetails": 0}
    r = requests.get("https://nominatim.openstreetmap.org/search",
                     params=params, headers=headers, timeout=15.0)
    r.raise_for_status()
    hits = r.json()
    if not hits:
        raise SystemExit(f"Address not found by Nominatim: {address!r}")
    time.sleep(1.05)
    return float(hits[0]["lon"]), float(hits[0]["lat"])


def _square_polygon_wkt(lon: float, lat: float, acres: float) -> tuple[str, float]:
    """Build a square WGS84 polygon centered on (lon, lat) with the
    requested acreage. Returns (geom_wkt, lot_area_sqft).

    Side length in feet derived from the requested acreage; lat/lon
    deltas converted via the local meridian/parallel scale at `lat`.
    Good enough for the analyzer's centroid-driven lookups; not a
    substitute for a real parcel polygon when wetland overlap matters.
    """
    side_ft = math.sqrt(max(acres, 0.01) * 43560.0)
    half_ft = side_ft / 2.0
    # 1 degree latitude ~= 364320 ft (constant near MA latitudes); 1 degree
    # longitude shrinks with cos(lat). Using these as local-tangent-plane
    # scales keeps the synthetic parcel close to its requested size.
    deg_per_ft_lat = 1.0 / 364320.0
    deg_per_ft_lon = 1.0 / (364320.0 * math.cos(math.radians(lat)))
    dlat = half_ft * deg_per_ft_lat
    dlon = half_ft * deg_per_ft_lon
    coords = [
        (lon - dlon, lat - dlat),
        (lon + dlon, lat - dlat),
        (lon + dlon, lat + dlat),
        (lon - dlon, lat + dlat),
        (lon - dlon, lat - dlat),
    ]
    wkt = "POLYGON((" + ", ".join(f"{x} {y}" for x, y in coords) + "))"
    return wkt, side_ft * side_ft


def _build_parcel(args) -> dict:
    if args.address:
        lon, lat = _geocode_address(args.address)
        print(f"[geocoded] {args.address!r} -> ({lon:.5f}, {lat:.5f})")
        label = args.label or args.address
    else:
        lon, lat = args.latlon
        label = args.label or f"({lat:.5f}, {lon:.5f})"

    geom_wkt, area_sqft = _square_polygon_wkt(lon, lat, args.acres)

    return {
        "parcel_id": args.parcel_id or "ad-hoc",
        "municipality_id": args.municipality or "ma_acton",
        "address": label,
        "zoning_code": args.zoning,
        "land_use_type": args.land_use,
        "lot_area_sqft": area_sqft,
        "lon": lon,
        "lat": lat,
        "geom_wkt": geom_wkt,
    }


# --- main ------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--latlon", nargs=2, type=float, metavar=("LAT", "LON"),
                     help="Latitude and longitude in WGS84 decimal degrees.")
    src.add_argument("--address", help='Free-form address; geocoded via OSM Nominatim.')
    p.add_argument("--acres", type=float, required=True,
                   help="Parcel acreage. Drives the acreage tier (edge / colo / hyperscale / campus).")
    p.add_argument("--zoning", default=None,
                   help="Zoning code (e.g. 'I-1'). Optional; affects zoning compatibility scoring.")
    p.add_argument("--land-use", dest="land_use", default=None,
                   help="MassGIS or local land-use code. Optional.")
    p.add_argument("--label", default=None,
                   help="Address label to print in the readout. Defaults to the input address or coords.")
    p.add_argument("--parcel-id", dest="parcel_id", default=None,
                   help="Parcel id label (cosmetic only; nothing is persisted).")
    p.add_argument("--municipality", default=None,
                   help="Municipality id label. Defaults to ma_acton.")
    p.add_argument("--json", action="store_true",
                   help="Emit the full report JSON instead of the formatted readout.")
    args = p.parse_args()

    # Reorder --latlon (LAT LON) to a consistent (lon, lat) pair.
    if args.latlon:
        lat, lon = args.latlon
        args.latlon = (lon, lat)

    parcel = _build_parcel(args)

    # Late imports so argparse errors don't drag in SQLAlchemy.
    from app.database import SessionLocal
    from app.engine.datacenter.analyzer import _build_report

    s = SessionLocal()
    try:
        report = _build_report(s, parcel)
    finally:
        s.close()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        # The formatted readout is computed on the report by analyzer.py.
        print(report.get("recommendationReadout") or "(no readout produced)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
