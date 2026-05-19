"""
Ingest MassGIS L3 parcel shapefile into Plinth SIP.

MassGIS L3 zip contains the parcel geometry layer:
  - M{ID}TaxPar.shp  — parcel BOUNDARIES  (geometry + LOC_ID only)

Assessor attributes (address, owner, zoning, lot size, etc.) come from either:
  A. M{ID}Assess.shp in the same zip (older MassGIS format)
  B. data/assessor_{municipality}.csv fetched by fetch_assessor.py (FY25+ format)

Both are joined to TaxPar on LOC_ID.

Usage:
    backend\\venv\\Scripts\\python.exe backend\\scripts\\ingest.py <path_to_zip_or_dir> [--municipality ma_acton]

If assessor CSV is missing, run first:
    backend\\venv\\Scripts\\python.exe backend\\scripts\\fetch_assessor.py --municipality ma_acton
"""

import sys
import os
import argparse
import zipfile
import tempfile
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pathlib import Path as _Path

from app.database import SessionLocal
from app.models.parcel import Parcel, ParcelGeometry
from app.models.scan_run import ScanRun
from app.models.municipality import Municipality
from app.ingestion.adapters.massgis import MASSGIS_ADAPTER, massgis_field_transform
from app.ingestion.normalizer import normalize_parcel


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def to_multipolygon_wkt(geom):
    """Convert any Shapely geometry to MultiPolygon WKT. Returns None if invalid."""
    if geom is None or geom.is_empty:
        return None
    try:
        if not geom.is_valid:
            geom = geom.buffer(0)
        if isinstance(geom, Polygon):
            geom = MultiPolygon([geom])
        elif not isinstance(geom, MultiPolygon):
            geom = MultiPolygon(
                [p for p in geom.geoms if isinstance(p, Polygon)]
                if hasattr(geom, 'geoms') else []
            )
        if geom.is_empty:
            return None
        return geom.wkt
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Shapefile finders
# ---------------------------------------------------------------------------

def _pick_layer(shp_files: list, priority_keywords: list, exclude_keywords: list = None) -> Path | None:
    """Pick the best matching shapefile by priority keyword list."""
    exclude_keywords = exclude_keywords or []
    for kw in priority_keywords:
        matches = [s for s in shp_files if kw.lower() in s.name.lower()]
        if matches:
            return matches[0]
    # fallback: exclude known non-matching layers
    if exclude_keywords:
        filtered = [s for s in shp_files if not any(ex.lower() in s.name.lower() for ex in exclude_keywords)]
        if filtered:
            return filtered[0]
    return None


def find_assessor_csv(municipality_id: str) -> str | None:
    """Look for a pre-fetched assessor CSV in the data/ folder."""
    script_dir = _Path(__file__).parent
    backend_dir = script_dir.parent
    data_dir = backend_dir.parent / "data"
    csv_path = data_dir / f"assessor_{municipality_id}.csv"
    if csv_path.exists():
        return str(csv_path)
    return None


def find_layers(path_str: str) -> tuple[str, str | None]:
    """
    Accept a .zip, .shp, or directory.
    Returns (taxpar_shp_path, assess_shp_path_or_None).
    """
    p = Path(path_str)

    if p.suffix.lower() == ".shp" and p.exists():
        # Single shapefile given — look for Assess sibling in same directory
        parent = p.parent
        shp_files = list(parent.rglob("*.shp"))
        assess = _pick_layer(shp_files, ["Assess", "assess"])
        return str(p), (str(assess) if assess and assess != p else None)

    if p.suffix.lower() == ".zip":
        extract_dir = Path(tempfile.mkdtemp(prefix="plinth_ingest_"))
        print(f"  Extracting {p.name}...")
        with zipfile.ZipFile(str(p), "r") as z:
            z.extractall(extract_dir)
        shp_files = list(extract_dir.rglob("*.shp"))
        if not shp_files:
            raise FileNotFoundError(f"No .shp files found inside {p.name}")

        print(f"  Shapefiles in zip: {[s.name for s in shp_files]}")

        taxpar = _pick_layer(shp_files, ["TaxPar", "taxpar"])
        if taxpar is None:
            # Fallback: any non-special file
            taxpar = _pick_layer(
                shp_files,
                [],
                exclude_keywords=["Assess", "Misc", "ROW", "Condo", "OtherLeg", "Node", "Pt", "Line"]
            ) or shp_files[0]

        assess = _pick_layer(shp_files, ["Assess", "assess"])

        print(f"  Parcel geometry layer: {taxpar.name}")
        if assess:
            print(f"  Assessor attribute layer: {assess.name}")
        else:
            print("  WARNING: No Assess layer found — assessor attributes will be NULL")

        return str(taxpar), (str(assess) if assess else None)

    if p.is_dir():
        shp_files = list(p.rglob("*.shp"))
        if not shp_files:
            raise FileNotFoundError(f"No .shp files in {p}")
        print(f"  Shapefiles found: {[s.name for s in shp_files]}")
        taxpar = _pick_layer(shp_files, ["TaxPar", "taxpar"]) or shp_files[0]
        assess = _pick_layer(shp_files, ["Assess", "assess"])
        return str(taxpar), (str(assess) if assess else None)

    raise FileNotFoundError(f"Cannot find shapefile at: {p}")


# ---------------------------------------------------------------------------
# Main ingestion
# ---------------------------------------------------------------------------

def ingest(taxpar_path: str, assess_path: str | None, municipality_id: str,
           dry_run: bool = False, assess_csv: str | None = None):
    db = SessionLocal()
    try:
        # Verify municipality exists
        muni = db.query(Municipality).filter(
            Municipality.municipality_id == municipality_id
        ).first()
        if not muni:
            print(f"ERROR: Municipality '{municipality_id}' not in database.")
            print("Run setup.bat first.")
            sys.exit(1)

        # ── 1. Read TaxPar (geometry) ──────────────────────────────────────
        print("\n  Reading TaxPar (geometry layer)...")
        gdf = gpd.read_file(taxpar_path)
        print(f"  Loaded {len(gdf)} features. CRS: {gdf.crs}")
        print(f"  TaxPar columns: {list(gdf.columns)}")

        # Filter to fee-simple parcels
        if "POLY_TYPE" in gdf.columns:
            before = len(gdf)
            gdf = gdf[gdf["POLY_TYPE"].str.strip().isin(["FEE"])].copy()
            print(f"  FEE parcels only: {before} ->{len(gdf)}")

        # Drop null/empty geometries
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
        print(f"  Valid geometries: {len(gdf)}")

        # Reproject to WGS84
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            print(f"  Reprojecting to WGS84...")
            gdf = gdf.to_crs(epsg=4326)

        # Ensure LOC_ID is a string for joining
        if "LOC_ID" in gdf.columns:
            gdf["LOC_ID"] = gdf["LOC_ID"].astype(str).str.strip()

        # ── 2. Read and join Assess (attribute layer) ──────────────────────
        # Prefer CSV from fetch_assessor.py if no shapefile Assess layer found
        if not assess_path and assess_csv:
            assess_path = assess_csv
            print(f"\n  Using assessor CSV: {assess_csv}")

        if assess_path and assess_path.endswith(".csv"):
            print(f"\n  Reading assessor CSV...")
            adf = pd.read_csv(assess_path, dtype=str)
            print(f"  Loaded {len(adf)} assessor records.")
            print(f"  Assess CSV columns: {list(adf.columns)}")

            if "LOC_ID" in adf.columns:
                adf["LOC_ID"] = adf["LOC_ID"].astype(str).str.strip()
                # Rename any columns that clash with TaxPar (except LOC_ID)
                taxpar_non_loc = [c for c in gdf.columns if c not in ("geometry", "LOC_ID")]
                rename_map = {col: f"ASSESS_{col}" for col in taxpar_non_loc if col in adf.columns}
                if rename_map:
                    adf = adf.rename(columns=rename_map)
                before_cols = len(gdf.columns)
                gdf = gdf.merge(adf, on="LOC_ID", how="left")
                print(f"  Joined CSV: {before_cols} ->{len(gdf.columns)} columns")
                if "SITE_ADDR" in gdf.columns:
                    matched = gdf["SITE_ADDR"].notna().sum()
                    print(f"  Parcels with address data: {matched}/{len(gdf)}")
                assess_path = None  # mark as handled

        if assess_path:
            print(f"\n  Reading Assess (attribute layer)...")
            adf = gpd.read_file(assess_path)
            print(f"  Loaded {len(adf)} assessor records.")
            print(f"  Assess columns: {list(adf.columns)}")

            if "LOC_ID" in adf.columns:
                adf["LOC_ID"] = adf["LOC_ID"].astype(str).str.strip()

                # Drop geometry from assess (we only want the attributes)
                attr_cols = [c for c in adf.columns if c != "geometry"]
                adf_attrs = adf[attr_cols].copy()

                # Rename any columns that clash with TaxPar non-LOC_ID columns
                taxpar_non_loc = [c for c in gdf.columns if c not in ("geometry", "LOC_ID")]
                for col in taxpar_non_loc:
                    if col in adf_attrs.columns:
                        adf_attrs = adf_attrs.rename(columns={col: f"ASSESS_{col}"})

                # Left join: every TaxPar parcel gets assess attributes where available
                before_cols = len(gdf.columns)
                gdf = gdf.merge(adf_attrs, on="LOC_ID", how="left")
                print(f"  Joined: {before_cols} ->{len(gdf.columns)} columns on TaxPar")
                print(f"  Columns after join: {[c for c in gdf.columns if c != 'geometry']}")

                # Count how many parcels got assessor data
                if "SITE_ADDR" in gdf.columns:
                    matched = gdf["SITE_ADDR"].notna().sum()
                    print(f"  Parcels with address data: {matched}/{len(gdf)}")
            else:
                print("  WARNING: Assess layer has no LOC_ID column — cannot join.")

        if dry_run:
            print(f"\nDRY RUN — would load {len(gdf)} parcels.")
            print("All columns after join:")
            for col in gdf.columns:
                if col != "geometry":
                    val = gdf[col].iloc[0] if len(gdf) > 0 else None
                    print(f"  {col}: {val}")
            return

        # ── 3. Create scan run ─────────────────────────────────────────────
        scan_run = ScanRun(
            municipality_id=municipality_id,
            config_version=1,
            status="running",
            run_type="ingest",
            triggered_by="script",
            started_at=datetime.now(timezone.utc),
        )
        db.add(scan_run)
        db.commit()
        db.refresh(scan_run)
        print(f"\n  Scan run: {scan_run.id}")

        # ── 4. Ingest parcels (bulk batched) ─────────────────────────
        print(f"  Loading {len(gdf)} parcels...")
        ingested = 0
        skipped = 0
        errors = 0
        BATCH_SIZE = 500

        parcel_batch = []
        geom_batch = []
        geom_keys_batch = []

        def _flush_batch():
            nonlocal parcel_batch, geom_batch, geom_keys_batch
            if parcel_batch:
                stmt = pg_insert(Parcel.__table__).values(parcel_batch)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_parcel_municipality",
                    set_={
                        "address": stmt.excluded.address,
                        "owner_name": stmt.excluded.owner_name,
                        "lot_area_sqft": stmt.excluded.lot_area_sqft,
                        "land_use_type": stmt.excluded.land_use_type,
                        "assessed_use": stmt.excluded.assessed_use,
                        "existing_building_footprint_area": stmt.excluded.existing_building_footprint_area,
                        "existing_structure_count": stmt.excluded.existing_structure_count,
                        "zoning_code": stmt.excluded.zoning_code,
                        "owner_mailing_address": stmt.excluded.owner_mailing_address,
                        "raw_source_references": stmt.excluded.raw_source_references,
                    }
                )
                db.execute(stmt)
            if geom_keys_batch:
                from sqlalchemy import tuple_
                db.query(ParcelGeometry).filter(
                    tuple_(ParcelGeometry.parcel_id, ParcelGeometry.municipality_id)
                    .in_(geom_keys_batch)
                ).delete(synchronize_session=False)
            if geom_batch:
                db.execute(ParcelGeometry.__table__.insert(), geom_batch)
            db.commit()
            parcel_batch = []
            geom_batch = []
            geom_keys_batch = []

        for idx, row in gdf.iterrows():
            try:
                geom = row.geometry
                if geom is None or geom.is_empty:
                    skipped += 1
                    continue

                raw = {col: row[col] for col in gdf.columns if col != "geometry"}
                transformed = massgis_field_transform(raw)

                normalized = normalize_parcel(
                    transformed, geom, MASSGIS_ADAPTER, municipality_id
                )
                if normalized is None or not normalized.get("parcel_id"):
                    skipped += 1
                    continue

                parcel_id = str(normalized["parcel_id"]).strip()

                raw_source = {
                    k: str(v) for k, v in transformed.items()
                    if v is not None and str(v) not in ("", "nan", "NULL", "None")
                }

                parcel_batch.append({
                    "parcel_id": parcel_id,
                    "municipality_id": municipality_id,
                    "address": normalized.get("address"),
                    "owner_name": normalized.get("owner_name"),
                    "owner_mailing_address": normalized.get("owner_mailing_address"),
                    "zoning_code": normalized.get("zoning_code"),
                    "lot_area_sqft": normalized.get("lot_area_sqft"),
                    "land_use_type": normalized.get("land_use_type"),
                    "assessed_use": normalized.get("assessed_use"),
                    "existing_building_footprint_area": normalized.get("existing_building_footprint_area"),
                    "existing_structure_count": normalized.get("existing_structure_count"),
                    "raw_source_references": {"source": "massgis_l3", "fields": raw_source},
                    "first_seen_scan_run_id": scan_run.id,
                })

                geom_wkt = to_multipolygon_wkt(geom)
                if geom_wkt:
                    geom_keys_batch.append((parcel_id, municipality_id))
                    geom_batch.append({
                        "parcel_id": parcel_id,
                        "municipality_id": municipality_id,
                        "geom": f"SRID=4326;{geom_wkt}",
                        "area_sqft_calculated": normalized.get("lot_area_sqft"),
                        "scan_run_id": scan_run.id,
                    })

                ingested += 1
                if len(parcel_batch) >= BATCH_SIZE:
                    _flush_batch()
                    print(f"    {ingested} loaded...")

            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"    Warning row {idx}: {e}")
                continue

        _flush_batch()

        scan_run.status = "complete"
        scan_run.parcels_ingested = ingested
        scan_run.completed_at = datetime.now(timezone.utc)
        db.commit()

        print(f"\n  Ingestion complete:")
        print(f"    Loaded:  {ingested}")
        print(f"    Skipped: {skipped}")
        print(f"    Errors:  {errors}")

    except Exception as e:
        db.rollback()
        print(f"\nFATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Ingest MassGIS L3 parcel data into Plinth SIP"
    )
    parser.add_argument("shapefile", help="Path to .zip, .shp, or directory containing MassGIS data")
    parser.add_argument("--municipality", default="ma_acton")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show columns without loading to DB")
    parser.add_argument("--assess-csv", default=None,
                        help="Path to assessor CSV from fetch_assessor.py (optional — auto-detected if in data/ folder)")
    args = parser.parse_args()

    print(f"Plinth SIP — Ingest")
    print(f"Municipality: {args.municipality}")
    print(f"File:         {args.shapefile}")

    taxpar_path, assess_path = find_layers(args.shapefile)

    # Auto-detect assessor CSV if no shapefile Assess layer found
    assess_csv = args.assess_csv
    if not assess_path and not assess_csv:
        assess_csv = find_assessor_csv(args.municipality)
        if assess_csv:
            print(f"  Auto-detected assessor CSV: {assess_csv}")
        else:
            print("  WARNING: No assessor data found.")
            print(f"  Run: venv\\Scripts\\python.exe scripts\\fetch_assessor.py --municipality {args.municipality}")

    ingest(taxpar_path, assess_path, args.municipality,
           dry_run=args.dry_run, assess_csv=assess_csv)


if __name__ == "__main__":
    main()
