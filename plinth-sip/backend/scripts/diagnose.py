"""
Diagnostic script — run this to see what's actually in the database.
Shows zoning codes, score distribution, and a sample parcel's rule results.

Usage:
    backend\venv\Scripts\python.exe backend\scripts\diagnose.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.parcel import Parcel, ParcelScore, ParcelRuleResult
from sqlalchemy import func

MUNICIPALITY = "ma_acton"

db = SessionLocal()
try:
    # ── 1. Unique zoning codes ───────────────────────────────────────────────
    print("=" * 60)
    print("ZONING CODES IN DATABASE")
    print("=" * 60)
    rows = (
        db.query(Parcel.zoning_code, func.count(Parcel.id).label("count"))
        .filter(Parcel.municipality_id == MUNICIPALITY)
        .group_by(Parcel.zoning_code)
        .order_by(func.count(Parcel.id).desc())
        .all()
    )
    for code, count in rows:
        print(f"  {str(code or 'NULL'):<20} {count:>6} parcels")

    # ── 2. Score distribution ────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("SCORE & TIER DISTRIBUTION")
    print("=" * 60)
    tier_rows = (
        db.query(ParcelScore.tier, func.count(ParcelScore.id))
        .filter(ParcelScore.municipality_id == MUNICIPALITY)
        .group_by(ParcelScore.tier)
        .order_by(ParcelScore.tier)
        .all()
    )
    for tier, count in tier_rows:
        print(f"  Tier {tier}: {count}")

    score_stats = (
        db.query(
            func.min(ParcelScore.score),
            func.max(ParcelScore.score),
            func.avg(ParcelScore.score),
        )
        .filter(ParcelScore.municipality_id == MUNICIPALITY)
        .first()
    )
    if score_stats:
        print(f"  Score min/max/avg: {score_stats[0]:.1f} / {score_stats[1]:.1f} / {float(score_stats[2]):.1f}")

    # ── 3. Sample parcel rule results ────────────────────────────────────────
    print()
    print("=" * 60)
    print("SAMPLE PARCEL — RULE RESULTS")
    print("=" * 60)
    sample = db.query(Parcel).filter(Parcel.municipality_id == MUNICIPALITY).first()
    if sample:
        print(f"  Parcel:      {sample.parcel_id}")
        print(f"  Address:     {sample.address}")
        print(f"  Zoning:      {sample.zoning_code}")
        print(f"  Lot area:    {float(sample.lot_area_sqft):,.0f} sqft" if sample.lot_area_sqft else "  Lot area:    None")
        print(f"  Bldg area:   {float(sample.existing_building_footprint_area):,.0f} sqft" if sample.existing_building_footprint_area else "  Bldg area:   None")
        print(f"  Structures:  {sample.existing_structure_count}")
        print()

        score = db.query(ParcelScore).filter(
            ParcelScore.municipality_id == MUNICIPALITY,
            ParcelScore.parcel_id == sample.parcel_id,
        ).order_by(ParcelScore.created_at.desc()).first()
        if score:
            print(f"  Score: {float(score.score):.1f}  Tier: {score.tier}")
            if score.score_breakdown:
                for cat, val in score.score_breakdown.items():
                    print(f"    {cat:<30} {val['score']:.0f}")

        rules = db.query(ParcelRuleResult).filter(
            ParcelRuleResult.municipality_id == MUNICIPALITY,
            ParcelRuleResult.parcel_id == sample.parcel_id,
        ).order_by(ParcelRuleResult.created_at.desc()).all()
        if rules:
            print()
            print(f"  Rules:")
            for r in rules:
                print(f"    [{r.result:<12}] {r.rule_id:<25} conf={r.confidence:.2f}")
                print(f"             {r.explanation[:90]}")

finally:
    db.close()
