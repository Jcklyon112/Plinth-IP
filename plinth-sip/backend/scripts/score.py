"""
Run scoring on all ingested parcels for a municipality.
Run this after ingest.py.

Usage:
    python scripts/score.py --municipality ma_acton
"""

import sys
import os
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.parcel import Parcel, ParcelGeometry, ParcelRuleResult, ParcelScore
from app.models.scan_run import ScanRun
from app.models.municipality import MunicipalityConfig
from app.models.template import PlinthTemplate
from app.engine.runner import evaluate_parcel
from geoalchemy2.shape import to_shape


def score_municipality(municipality_id: str):
    db = SessionLocal()
    try:
        # Get active config
        config_record = db.query(MunicipalityConfig).filter(
            MunicipalityConfig.municipality_id == municipality_id,
            MunicipalityConfig.active == True,
        ).first()
        if not config_record:
            print(f"ERROR: No active config found for '{municipality_id}'.")
            print("Make sure setup.bat ran successfully and seeded the config.")
            sys.exit(1)

        config_data = config_record.config_data
        print(f"  Using config version {config_record.version}")

        # Get active templates
        templates = db.query(PlinthTemplate).filter(
            PlinthTemplate.active_status == True
        ).all()
        template_dicts = [
            {
                "template_id": t.template_id,
                "template_name": t.template_name,
                "footprint_area_sqft": float(t.footprint_area_sqft),
                "active_status": t.active_status,
            }
            for t in templates
        ]
        print(f"  Templates: {[t['template_id'] for t in template_dicts]}")

        # Get all parcels
        parcels = db.query(Parcel).filter(
            Parcel.municipality_id == municipality_id
        ).all()
        total = len(parcels)
        print(f"  Parcels to score: {total}")

        # Bulk-fetch geometries to avoid N+1 queries
        geom_records = db.query(ParcelGeometry).filter(
            ParcelGeometry.municipality_id == municipality_id
        ).all()
        geometry_by_parcel_id = {}
        for g in geom_records:
            try:
                geometry_by_parcel_id[g.parcel_id] = to_shape(g.geom)
            except Exception:
                pass
        print(f"  Geometries loaded: {len(geometry_by_parcel_id)}")

        # Get calc_epsg from config
        calc_crs = config_data.get("calc_crs", "EPSG:26986")
        try:
            calc_epsg = int(calc_crs.replace("EPSG:", "").replace("epsg:", ""))
        except (ValueError, AttributeError):
            calc_epsg = 26986

        if total == 0:
            print("  No parcels found. Run ingest.py first.")
            sys.exit(1)

        # Create scan run
        scan_run = ScanRun(
            municipality_id=municipality_id,
            config_version=config_record.version,
            status="running",
            run_type="rescore",
            triggered_by="script",
            started_at=datetime.now(timezone.utc),
        )
        db.add(scan_run)
        db.commit()
        db.refresh(scan_run)
        scan_run_id = str(scan_run.id)

        scored = 0
        errors = 0

        for parcel in parcels:
            try:
                parcel_dict = {
                    "parcel_id": parcel.parcel_id,
                    "lot_area_sqft": float(parcel.lot_area_sqft) if parcel.lot_area_sqft else None,
                    "zoning_code": parcel.zoning_code,
                    "land_use_type": parcel.land_use_type,
                    "assessed_use": parcel.assessed_use,
                    "existing_building_footprint_area": float(parcel.existing_building_footprint_area) if parcel.existing_building_footprint_area else None,
                    "existing_structure_count": parcel.existing_structure_count,
                    "constraints_flags": [],
                    "geometry_shapely": geometry_by_parcel_id.get(parcel.parcel_id),
                    "calc_epsg": calc_epsg,
                }

                result = evaluate_parcel(parcel_dict, config_data, template_dicts)

                # Delete old results for this parcel
                db.query(ParcelRuleResult).filter(
                    ParcelRuleResult.parcel_id == parcel.parcel_id,
                    ParcelRuleResult.municipality_id == municipality_id,
                ).delete()
                db.query(ParcelScore).filter(
                    ParcelScore.parcel_id == parcel.parcel_id,
                    ParcelScore.municipality_id == municipality_id,
                ).delete()

                # Store rule results
                for rr in result["rule_results"]:
                    db.add(ParcelRuleResult(
                        parcel_id=parcel.parcel_id,
                        municipality_id=municipality_id,
                        scan_run_id=scan_run.id,
                        rule_id=rr["rule_id"],
                        rule_category=rr["rule_category"],
                        result=rr["result"],
                        explanation=rr["explanation"],
                        assumptions_used=rr["assumptions_used"],
                        confidence=rr["confidence"],
                    ))

                # Store score
                sr = result["score_record"]
                db.add(ParcelScore(
                    parcel_id=parcel.parcel_id,
                    municipality_id=municipality_id,
                    scan_run_id=scan_run.id,
                    scoring_profile=sr["scoring_profile"],
                    score=sr["score"],
                    tier=sr["tier"],
                    score_breakdown=sr["score_breakdown"],
                    confidence=sr["confidence"],
                    template_fits=sr["template_fits"],
                    blockers=sr["blockers"],
                ))

                scored += 1
                if scored % 200 == 0:
                    db.commit()
                    print(f"    ...{scored}/{total} scored")

            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"    Warning: scoring error on {parcel.parcel_id}: {e}")
                continue

        db.commit()

        # Update scan run
        scan_run.status = "complete"
        scan_run.parcels_scored = scored
        scan_run.completed_at = datetime.now(timezone.utc)
        db.commit()

        # Print tier summary
        tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        scores = db.query(ParcelScore).filter(
            ParcelScore.municipality_id == municipality_id,
            ParcelScore.scan_run_id == scan_run.id,
        ).all()
        for s in scores:
            if s.tier in tier_counts:
                tier_counts[s.tier] += 1

        print(f"\n  Scoring complete:")
        print(f"    Scored:  {scored} parcels")
        print(f"    Errors:  {errors}")
        print(f"\n  Tier breakdown:")
        print(f"    Tier 1 (Immediate):    {tier_counts[1]}")
        print(f"    Tier 2 (Review):       {tier_counts[2]}")
        print(f"    Tier 3 (Conditional):  {tier_counts[3]}")
        print(f"    Tier 4 (Low Priority): {tier_counts[4]}")
        print(f"\n  Open http://localhost:3000 to see the map.")

    except Exception as e:
        db.rollback()
        print(f"\nFATAL ERROR: {e}")
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Score parcels for a municipality")
    parser.add_argument("--municipality", default="ma_acton", help="Municipality ID (default: ma_acton)")
    args = parser.parse_args()

    print(f"Plinth SIP - Parcel Scoring")
    print(f"Municipality: {args.municipality}")
    print()

    score_municipality(args.municipality)


if __name__ == "__main__":
    main()
