"""
Generic Parcel Ingestor
Ingests parcels from a GeoDataFrame using a state GIS registry config.

Used by the Phase 5 auto-scan system for any state.
Works alongside the existing massgis-specific ingest.py for MA.

Field mapping uses state_gis_registry field_map:
    { "parcel_id": "MAPPAR", "address": "LOCATION", ... }
"""

import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from shapely.geometry import Polygon, MultiPolygon

from sqlalchemy.dialects.postgresql import insert as pg_insert
import geopandas as gpd

from app.database import SessionLocal
from app.models.parcel import Parcel, ParcelGeometry
from app.models.scan_run import ScanRun
from app.models.municipality import Municipality


# ---------------------------------------------------------------------------
# Geometry helpers (shared with ingest.py pattern)
# ---------------------------------------------------------------------------

def _to_multipolygon_wkt(geom):
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
# Field extraction helpers
# ---------------------------------------------------------------------------

def _is_empty(val) -> bool:
    return val is None or str(val).strip() in ("", "None", "NULL", "null", "nan", "NaN")


def _get_field(row: dict, field_name: str | None):
    if not field_name or field_name not in row:
        return None
    val = row.get(field_name)
    return None if _is_empty(val) else val


def _normalize_lot_size(val, unit: str) -> float | None:
    """Convert lot size to sqft."""
    try:
        f = float(str(val).replace(",", ""))
        if unit == "acres":
            return f * 43560
        return f
    except (ValueError, TypeError):
        return None


def _normalize_use_code(raw_code: str, state_cfg: dict) -> str | None:
    """Map raw use code to internal land use type using STANDARD_USE_CODE_MAP."""
    from app.agents.state_gis_registry import STANDARD_USE_CODE_MAP
    if not raw_code:
        return None
    code_str = str(raw_code).strip()
    # Try as-is first
    result = STANDARD_USE_CODE_MAP.get(code_str)
    if result:
        return result
    # Try stripping to 3 digits (MA 4-digit → 3-digit)
    if len(code_str) == 4 and code_str.isdigit():
        result = STANDARD_USE_CODE_MAP.get(code_str[:3])
        if result:
            return result
    # Try base code for compound codes (e.g. "11-70" → "11")
    if "-" in code_str:
        base = code_str.split("-")[0].strip()
        result = STANDARD_USE_CODE_MAP.get(base)
        if result:
            return result
    # Try uppercase (VT CAT codes are case-sensitive in map)
    result = STANDARD_USE_CODE_MAP.get(code_str.upper())
    if result:
        return result
    return code_str  # Return raw code if no mapping found


# ---------------------------------------------------------------------------
# Main ingest function
# ---------------------------------------------------------------------------

def ingest_from_gdf(
    gdf: gpd.GeoDataFrame,
    state_cfg: dict,
    municipality_id: str,
    dry_run: bool = False,
) -> dict:
    """
    Ingest parcels from a GeoDataFrame into the Plinth SIP database.

    Args:
        gdf: GeoDataFrame in WGS84 (EPSG:4326) with raw source columns
        state_cfg: State config from state_gis_registry (has field_map, lot_size_unit)
        municipality_id: e.g. "vt_burlington"
        dry_run: If True, print sample without writing to DB

    Returns:
        dict with ingested/skipped/error counts
    """
    field_map = state_cfg.get("field_map", {})
    lot_unit = state_cfg.get("lot_size_unit", "sqft")

    if dry_run:
        print(f"\nDRY RUN: would ingest {len(gdf)} parcels for {municipality_id}")
        print(f"Field map: {field_map}")
        if len(gdf) > 0:
            row = gdf.iloc[0]
            print("\nSample row mapping:")
            for internal, source in field_map.items():
                if source in row:
                    print(f"  {internal} ({source}): {row[source]}")
        return {"ingested": 0, "skipped": 0, "errors": 0, "dry_run": True}

    db = SessionLocal()
    try:
        # Verify municipality exists
        muni = db.query(Municipality).filter(
            Municipality.municipality_id == municipality_id
        ).first()
        if not muni:
            raise RuntimeError(
                f"Municipality '{municipality_id}' not in database. "
                "Run the auto-scan setup step first."
            )

        # Reproject to WGS84 if needed
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            print(f"  Reprojecting from {gdf.crs} to WGS84...")
            gdf = gdf.to_crs(epsg=4326)

        # Create scan run
        scan_run = ScanRun(
            municipality_id=municipality_id,
            config_version=1,
            status="running",
            run_type="ingest",
            triggered_by="auto_scan",
            started_at=datetime.now(timezone.utc),
        )
        db.add(scan_run)
        db.commit()
        db.refresh(scan_run)
        print(f"  Scan run: {scan_run.id}")

        ingested = 0
        skipped = 0
        errors = 0
        BATCH_SIZE = 500

        parcel_batch = []
        geom_batch = []
        geom_keys_batch = []  # (parcel_id, municipality_id) for bulk delete

        source_name = f"{state_cfg.get('name', 'unknown')}_arcgis_rest"

        def _flush_batch():
            nonlocal parcel_batch, geom_batch, geom_keys_batch
            if parcel_batch:
                # Bulk upsert parcels
                stmt = pg_insert(Parcel.__table__).values(parcel_batch)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_parcel_municipality",
                    set_={
                        "address": stmt.excluded.address,
                        "owner_name": stmt.excluded.owner_name,
                        "lot_area_sqft": stmt.excluded.lot_area_sqft,
                        "land_use_type": stmt.excluded.land_use_type,
                        "existing_building_footprint_area": stmt.excluded.existing_building_footprint_area,
                        "existing_structure_count": stmt.excluded.existing_structure_count,
                        "zoning_code": stmt.excluded.zoning_code,
                        "owner_mailing_address": stmt.excluded.owner_mailing_address,
                        "raw_source_references": stmt.excluded.raw_source_references,
                    }
                )
                db.execute(stmt)
            if geom_keys_batch:
                # Bulk delete old geometries
                from sqlalchemy import tuple_
                db.query(ParcelGeometry).filter(
                    tuple_(ParcelGeometry.parcel_id, ParcelGeometry.municipality_id)
                    .in_(geom_keys_batch)
                ).delete(synchronize_session=False)
            if geom_batch:
                # Bulk insert geometries
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

                # Extract fields using field_map
                parcel_id_raw = _get_field(raw, field_map.get("parcel_id"))
                if not parcel_id_raw:
                    skipped += 1
                    continue

                parcel_id = str(parcel_id_raw).strip()

                address = _get_field(raw, field_map.get("address"))
                owner_name = _get_field(raw, field_map.get("owner_name"))
                owner_mail = _get_field(raw, field_map.get("owner_mail"))
                zoning_code_raw = _get_field(raw, field_map.get("zoning_code"))
                lot_size_raw = _get_field(raw, field_map.get("lot_size"))
                use_code_raw = _get_field(raw, field_map.get("use_code"))
                bld_area_raw = _get_field(raw, field_map.get("bld_area"))
                units_raw = _get_field(raw, field_map.get("units"))

                lot_area_sqft = _normalize_lot_size(lot_size_raw, lot_unit)
                land_use_type = _normalize_use_code(str(use_code_raw) if use_code_raw else None, state_cfg)

                # Building footprint from bld_area
                bld_area_sqft = None
                if bld_area_raw:
                    try:
                        bld_area_sqft = float(str(bld_area_raw).replace(",", ""))
                    except (ValueError, TypeError):
                        pass

                # Unit count
                structure_count = None
                if units_raw:
                    try:
                        structure_count = int(float(str(units_raw)))
                    except (ValueError, TypeError):
                        pass

                # Zoning code — strip whitespace
                zoning_code = str(zoning_code_raw).strip() if zoning_code_raw else None

                # Build clean raw source dict for auditing
                raw_source = {
                    k: str(v) for k, v in raw.items()
                    if v is not None and str(v) not in ("", "nan", "NULL", "None")
                }

                parcel_batch.append({
                    "parcel_id": parcel_id,
                    "municipality_id": municipality_id,
                    "address": address,
                    "owner_name": owner_name,
                    "owner_mailing_address": owner_mail,
                    "zoning_code": zoning_code,
                    "lot_area_sqft": lot_area_sqft,
                    "land_use_type": land_use_type,
                    "assessed_use": None,
                    "existing_building_footprint_area": bld_area_sqft,
                    "existing_structure_count": structure_count,
                    "raw_source_references": {
                        "source": source_name,
                        "fields": raw_source,
                    },
                    "first_seen_scan_run_id": scan_run.id,
                })

                # Geometry
                geom_wkt = _to_multipolygon_wkt(geom)
                if geom_wkt:
                    geom_keys_batch.append((parcel_id, municipality_id))
                    geom_batch.append({
                        "parcel_id": parcel_id,
                        "municipality_id": municipality_id,
                        "geom": f"SRID=4326;{geom_wkt}",
                        "area_sqft_calculated": lot_area_sqft,
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

        result = {
            "scan_run_id": str(scan_run.id),
            "ingested": ingested,
            "skipped": skipped,
            "errors": errors,
        }

        print(f"\n  Ingestion complete:")
        print(f"    Loaded:  {ingested}")
        print(f"    Skipped: {skipped}")
        print(f"    Errors:  {errors}")

        return result

    except Exception as e:
        db.rollback()
        print(f"\nFATAL: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        db.close()
