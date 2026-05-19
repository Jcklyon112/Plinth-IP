"""
NY Polygon Accuracy Test Harness
================================
Tests parcel scoring accuracy across multiple NY regions by:
1. Fetching real parcels from NY ArcGIS with polygon spatial filters
2. Scoring them with the current engine
3. Reporting tier distributions, confidence metrics, and rule-level breakdowns
4. Identifying the biggest accuracy gaps

Run from plinth-sip/backend/:
    python test_ny_accuracy.py

Each cycle prints a detailed report. The script runs continuously until stopped.
"""

import json
import os
import sys
import time
import statistics
from pathlib import Path
from collections import Counter

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from app.agents.gis_fetcher import fetch_parcels_in_polygon, normalize_arcgis_feature
from app.agents.state_gis_registry import get_state_config
from app.agents.auto_config import (
    generate_municipality_config, discover_zoning_codes, classify_zoning_code,
    STATE_DEFAULTS, DISTRICT_TEMPLATES
)
from app.engine.runner import evaluate_parcel, normalize_land_use_type
from app.engine.parallel_scorer import score_parcels_parallel

# ---------------------------------------------------------------------------
# Test polygons — diverse NY regions
# ---------------------------------------------------------------------------

TEST_POLYGONS = {
    # ── NYC / Urban ───────────────────────────────────────────────────────
    "brooklyn_park_slope": {
        "label": "Brooklyn - Park Slope (dense urban, sewer, small lots)",
        "region_type": "urban_nyc",
        "expected_sewer": True,
        "expected_lot_range": (1000, 5000),
        "polygon": {
            "type": "Polygon",
            "coordinates": [[
                [-73.980, 40.672],
                [-73.975, 40.672],
                [-73.975, 40.677],
                [-73.980, 40.677],
                [-73.980, 40.672],
            ]]
        }
    },
    "queens_flushing": {
        "label": "Queens - Flushing (urban residential, sewer, mixed density)",
        "region_type": "urban_nyc",
        "expected_sewer": True,
        "expected_lot_range": (2000, 8000),
        "polygon": {
            "type": "Polygon",
            "coordinates": [[
                [-73.835, 40.758],
                [-73.828, 40.758],
                [-73.828, 40.763],
                [-73.835, 40.763],
                [-73.835, 40.758],
            ]]
        }
    },
    "staten_island_tottenville": {
        "label": "Staten Island - Tottenville (suburban NYC, sewer, larger lots)",
        "region_type": "suburban_nyc",
        "expected_sewer": True,
        "expected_lot_range": (3000, 15000),
        "polygon": {
            "type": "Polygon",
            "coordinates": [[
                [-74.245, 40.500],
                [-74.238, 40.500],
                [-74.238, 40.506],
                [-74.245, 40.506],
                [-74.245, 40.500],
            ]]
        }
    },

    # ── Suburban ──────────────────────────────────────────────────────────
    "westchester_scarsdale": {
        "label": "Scarsdale, Westchester (affluent suburb, sewer, 10k-40k sqft lots)",
        "region_type": "suburban",
        "expected_sewer": True,
        "expected_lot_range": (10000, 60000),
        "polygon": {
            "type": "Polygon",
            "coordinates": [[
                [-73.800, 40.975],
                [-73.790, 40.975],
                [-73.790, 40.982],
                [-73.800, 40.982],
                [-73.800, 40.975],
            ]]
        }
    },
    "long_island_huntington": {
        "label": "Huntington, Long Island (suburban, sewer, 8k-20k sqft lots)",
        "region_type": "suburban",
        "expected_sewer": True,
        "expected_lot_range": (6000, 30000),
        "polygon": {
            "type": "Polygon",
            "coordinates": [[
                [-73.415, 40.870],
                [-73.405, 40.870],
                [-73.405, 40.876],
                [-73.415, 40.876],
                [-73.415, 40.870],
            ]]
        }
    },

    # ── Rural Upstate ─────────────────────────────────────────────────────
    "hudson_valley_rhinebeck": {
        "label": "Rhinebeck, Dutchess County (rural, septic, 1+ acre lots)",
        "region_type": "rural",
        "expected_sewer": False,
        "expected_lot_range": (20000, 200000),
        "polygon": {
            "type": "Polygon",
            "coordinates": [[
                [-73.915, 41.925],
                [-73.905, 41.925],
                [-73.905, 41.932],
                [-73.915, 41.932],
                [-73.915, 41.925],
            ]]
        }
    },
    "catskills_woodstock": {
        "label": "Woodstock, Ulster County (rural/tourist, septic, varied lots)",
        "region_type": "rural",
        "expected_sewer": False,
        "expected_lot_range": (10000, 200000),
        "polygon": {
            "type": "Polygon",
            "coordinates": [[
                [-74.125, 42.035],
                [-74.115, 42.035],
                [-74.115, 42.042],
                [-74.125, 42.042],
                [-74.125, 42.035],
            ]]
        }
    },

    # ── Mid-density towns ─────────────────────────────────────────────────
    "sag_harbor": {
        "label": "Sag Harbor, Suffolk County (village, mixed sewer/septic)",
        "region_type": "village",
        "expected_sewer": None,  # mixed
        "expected_lot_range": (4000, 40000),
        "polygon": {
            "type": "Polygon",
            "coordinates": [[
                [-72.300, 40.998],
                [-72.290, 40.998],
                [-72.290, 41.005],
                [-72.300, 41.005],
                [-72.300, 40.998],
            ]]
        }
    },
}


# ---------------------------------------------------------------------------
# Plinth templates (same as shape_analyzer.py)
# ---------------------------------------------------------------------------

PLINTH_TEMPLATES = [
    {"template_id": "plinth_studio", "template_name": "Plinth Studio",
     "footprint_area_sqft": 525, "bedrooms": 0, "active_status": True},
    {"template_id": "plinth_1br", "template_name": "Plinth 1BR",
     "footprint_area_sqft": 525, "bedrooms": 1, "active_status": True},
]


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def fetch_and_score_polygon(name: str, test_case: dict, cycle: int = 1) -> dict:
    """Fetch parcels from ArcGIS and score them. Returns analysis results."""
    polygon = test_case["polygon"]
    label = test_case["label"]

    print(f"\n{'='*70}")
    print(f"  TEST: {label}")
    print(f"  Region: {test_case['region_type']} | Expected sewer: {test_case['expected_sewer']}")
    print(f"{'='*70}")

    # Fetch parcels
    t0 = time.time()
    try:
        raw_features = fetch_parcels_in_polygon("NY", polygon, max_parcels=2000)
    except Exception as e:
        print(f"  FETCH FAILED: {e}")
        return {"name": name, "error": str(e), "parcels": 0}

    fetch_time = time.time() - t0
    print(f"  Fetched {len(raw_features)} features in {fetch_time:.1f}s")

    if not raw_features:
        return {"name": name, "error": "No features", "parcels": 0}

    # Normalize
    municipality_id = f"ny_{name}"
    normalized = []
    for f in raw_features:
        p = normalize_arcgis_feature(f, "NY", municipality_id)
        if p and p.get("parcel_id"):
            p["geometry"] = f.get("geometry")
            normalized.append(p)

    print(f"  Normalized {len(normalized)} parcels")

    # Discover zoning codes
    zoning_codes = list({
        str(p.get("zoning_code", "")).strip()
        for p in normalized
        if p.get("zoning_code") and str(p["zoning_code"]).strip()
    })
    print(f"  Discovered zoning/prop codes: {sorted(zoning_codes)[:20]}")

    # Compute per-district lot stats
    import numpy as np
    district_lot_stats = {}
    for code in zoning_codes:
        lots = [p["lot_area_sqft"] for p in normalized
                if p.get("zoning_code") == code and p.get("lot_area_sqft") and p["lot_area_sqft"] > 0]
        if len(lots) >= 3:
            district_lot_stats[code] = {
                "median": float(np.median(lots)),
                "p25": float(np.percentile(lots, 25)),
                "p75": float(np.percentile(lots, 75)),
                "count": len(lots),
            }

    # Generate config
    config = generate_municipality_config(
        municipality_id=municipality_id,
        municipality_name=name.replace("_", " ").title(),
        state="NY",
        county=test_case.get("county", ""),
        zoning_codes=zoning_codes,
        median_lot_sqft=None,
        district_lot_stats=district_lot_stats,
    )

    # Score
    t1 = time.time()
    scored = score_parcels_parallel(normalized, config, templates=PLINTH_TEMPLATES, max_workers=8)
    score_time = time.time() - t1
    print(f"  Scored {len(scored)} parcels in {score_time:.1f}s")

    # Analyze results
    return analyze_results(name, test_case, scored, config, fetch_time, score_time)


def analyze_results(name: str, test_case: dict, scored: list, config: dict,
                    fetch_time: float, score_time: float) -> dict:
    """Analyze scoring results and identify accuracy gaps."""

    # Tier distribution
    tier_counts = Counter(p.get("tier") for p in scored)
    total = len(scored)

    # Score distribution
    scores = [p["score"] for p in scored if p.get("score") is not None]
    confidences = [p["confidence"] for p in scored if p.get("confidence") is not None]

    # Lot size analysis
    lots = [p["lot_area_sqft"] for p in scored if p.get("lot_area_sqft") and p["lot_area_sqft"] > 0]

    # Prop class distribution
    prop_classes = Counter(str(p.get("zoning_code", "?")) for p in scored)

    # Rule-level analysis
    rule_results_agg = {}
    for p in scored:
        for rr in (p.get("rule_results") or {}).values():
            rid = rr.rule_id
            if rid not in rule_results_agg:
                rule_results_agg[rid] = {"pass": 0, "conditional": 0, "fail": 0, "unknown": 0, "confidences": []}
            rule_results_agg[rid][rr.result] = rule_results_agg[rid].get(rr.result, 0) + 1
            rule_results_agg[rid]["confidences"].append(rr.confidence)

    # Blocker analysis
    blocker_counts = Counter()
    for p in scored:
        for b in (p.get("blockers") or []):
            if isinstance(b, dict):
                blocker_counts[b.get("rule_id", "?")] += 1

    # Expected lot range check
    exp_lo, exp_hi = test_case.get("expected_lot_range", (0, 999999))
    lots_in_range = sum(1 for l in lots if exp_lo <= l <= exp_hi) if lots else 0
    lot_range_pct = lots_in_range / len(lots) * 100 if lots else 0

    # Sewer check
    config_sewer = config.get("sewer_service", False)
    expected_sewer = test_case.get("expected_sewer")
    sewer_correct = (expected_sewer is None) or (config_sewer == expected_sewer)

    # Print report
    print(f"\n  --- RESULTS: {test_case['label']} ---")
    print(f"  Parcels: {total} | Fetch: {fetch_time:.1f}s | Score: {score_time:.1f}s")
    print(f"\n  TIER DISTRIBUTION:")
    for t in [1, 2, 3, 4]:
        ct = tier_counts.get(t, 0)
        pct = ct / total * 100 if total else 0
        bar = "█" * int(pct / 2)
        print(f"    Tier {t}: {ct:4d} ({pct:5.1f}%) {bar}")

    if scores:
        print(f"\n  SCORE STATS:")
        print(f"    Mean: {statistics.mean(scores):.1f} | Median: {statistics.median(scores):.1f}")
        print(f"    StdDev: {statistics.stdev(scores):.1f}" if len(scores) > 1 else "")
        print(f"    Min: {min(scores):.1f} | Max: {max(scores):.1f}")

    if confidences:
        print(f"\n  CONFIDENCE STATS:")
        print(f"    Mean: {statistics.mean(confidences):.3f} | Median: {statistics.median(confidences):.3f}")

    if lots:
        print(f"\n  LOT SIZE (sqft):")
        print(f"    Mean: {statistics.mean(lots):,.0f} | Median: {statistics.median(lots):,.0f}")
        print(f"    Min: {min(lots):,.0f} | Max: {max(lots):,.0f}")
        print(f"    In expected range ({exp_lo:,}-{exp_hi:,}): {lot_range_pct:.0f}%")

    print(f"\n  PROP CLASS DISTRIBUTION:")
    for code, ct in prop_classes.most_common(10):
        pct = ct / total * 100
        print(f"    {code:>5s}: {ct:4d} ({pct:5.1f}%)")

    print(f"\n  SEWER: config={config_sewer} | expected={expected_sewer} | {'CORRECT' if sewer_correct else 'WRONG'}")

    print(f"\n  RULE BREAKDOWN:")
    for rid, stats in sorted(rule_results_agg.items()):
        total_r = stats["pass"] + stats["conditional"] + stats["fail"] + stats["unknown"]
        pass_pct = stats["pass"] / total_r * 100 if total_r else 0
        fail_pct = stats["fail"] / total_r * 100 if total_r else 0
        unk_pct = stats["unknown"] / total_r * 100 if total_r else 0
        avg_conf = statistics.mean(stats["confidences"]) if stats["confidences"] else 0
        print(f"    {rid:25s}: pass={pass_pct:5.1f}% fail={fail_pct:5.1f}% unk={unk_pct:5.1f}% conf={avg_conf:.3f}")

    if blocker_counts:
        print(f"\n  TOP BLOCKERS:")
        for rule_id, ct in blocker_counts.most_common(5):
            print(f"    {rule_id}: {ct} parcels blocked")

    # Accuracy issues
    issues = []
    if not sewer_correct:
        issues.append(f"SEWER WRONG: config={config_sewer}, expected={expected_sewer}")
    if confidences and statistics.mean(confidences) < 0.3:
        issues.append(f"LOW CONFIDENCE: mean={statistics.mean(confidences):.3f}")
    if lots and lot_range_pct < 50:
        issues.append(f"LOT SIZES OUT OF RANGE: only {lot_range_pct:.0f}% in expected range")

    # Check if too many parcels are unknown
    for rid, stats in rule_results_agg.items():
        total_r = stats["pass"] + stats["conditional"] + stats["fail"] + stats["unknown"]
        if total_r > 0 and stats["unknown"] / total_r > 0.5:
            issues.append(f"RULE {rid}: {stats['unknown']/total_r*100:.0f}% unknown")

    if issues:
        print(f"\n  ACCURACY ISSUES:")
        for iss in issues:
            print(f"    - {iss}")

    return {
        "name": name,
        "label": test_case["label"],
        "region_type": test_case["region_type"],
        "parcels": total,
        "tier_counts": dict(tier_counts),
        "mean_score": statistics.mean(scores) if scores else 0,
        "mean_confidence": statistics.mean(confidences) if confidences else 0,
        "median_lot_sqft": statistics.median(lots) if lots else 0,
        "sewer_correct": sewer_correct,
        "issues": issues,
        "fetch_time": fetch_time,
        "score_time": score_time,
        "prop_classes": dict(prop_classes),
        "blocker_counts": dict(blocker_counts),
        "rule_stats": {
            rid: {k: v for k, v in stats.items() if k != "confidences"}
            for rid, stats in rule_results_agg.items()
        },
    }


def print_cycle_summary(results: list[dict], cycle: int):
    """Print a cross-region summary for one cycle."""
    print(f"\n{'#'*70}")
    print(f"  CYCLE {cycle} SUMMARY")
    print(f"{'#'*70}")

    total_parcels = sum(r["parcels"] for r in results if "parcels" in r)
    total_issues = sum(len(r.get("issues", [])) for r in results)
    sewer_correct = sum(1 for r in results if r.get("sewer_correct"))
    sewer_total = sum(1 for r in results if "sewer_correct" in r)
    mean_conf = statistics.mean([r["mean_confidence"] for r in results if r.get("mean_confidence")]) if results else 0

    print(f"  Total parcels analyzed: {total_parcels}")
    print(f"  Regions tested: {len(results)}")
    print(f"  Sewer accuracy: {sewer_correct}/{sewer_total}")
    print(f"  Mean confidence: {mean_conf:.3f}")
    print(f"  Total accuracy issues: {total_issues}")

    print(f"\n  PER-REGION:")
    print(f"  {'Region':<30s} {'Parcels':>8s} {'T1':>5s} {'T2':>5s} {'T3':>5s} {'T4':>5s} {'Score':>6s} {'Conf':>6s} {'Issues':>6s}")
    print(f"  {'-'*88}")
    for r in results:
        if r.get("error"):
            print(f"  {r['name']:<30s} {'ERROR':>8s}  {r['error'][:50]}")
            continue
        tc = r.get("tier_counts", {})
        print(f"  {r['name']:<30s} {r['parcels']:>8d} {tc.get(1,0):>5d} {tc.get(2,0):>5d} {tc.get(3,0):>5d} {tc.get(4,0):>5d} {r['mean_score']:>6.1f} {r['mean_confidence']:>6.3f} {len(r.get('issues',[])):>6d}")

    # Aggregate blocker analysis
    print(f"\n  AGGREGATE BLOCKERS (across all regions):")
    all_blockers = Counter()
    for r in results:
        for rule_id, ct in r.get("blocker_counts", {}).items():
            all_blockers[rule_id] += ct
    for rule_id, ct in all_blockers.most_common(8):
        print(f"    {rule_id}: {ct} parcels")

    # Aggregate issues
    if total_issues > 0:
        print(f"\n  ALL ISSUES:")
        for r in results:
            for iss in r.get("issues", []):
                print(f"    [{r['name']}] {iss}")

    return {
        "cycle": cycle,
        "total_parcels": total_parcels,
        "mean_confidence": mean_conf,
        "sewer_accuracy": sewer_correct / sewer_total if sewer_total else 0,
        "total_issues": total_issues,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("  PLINTH SIP — NY POLYGON ACCURACY TEST HARNESS")
    print("  Testing parcel scoring accuracy across NY regions")
    print("=" * 70)

    cycle = 0
    cycle_summaries = []

    # Run continuously
    while True:
        cycle += 1
        print(f"\n\n{'*'*70}")
        print(f"  STARTING CYCLE {cycle}")
        print(f"{'*'*70}")

        results = []
        for name, test_case in TEST_POLYGONS.items():
            try:
                result = fetch_and_score_polygon(name, test_case, cycle)
                results.append(result)
            except Exception as e:
                print(f"  ERROR in {name}: {e}")
                import traceback
                traceback.print_exc()
                results.append({"name": name, "error": str(e), "parcels": 0})

        summary = print_cycle_summary(results, cycle)
        cycle_summaries.append(summary)

        # Print improvement trend
        if len(cycle_summaries) > 1:
            print(f"\n  TREND (cycles 1-{cycle}):")
            for cs in cycle_summaries:
                print(f"    Cycle {cs['cycle']}: conf={cs['mean_confidence']:.3f} issues={cs['total_issues']} sewer={cs['sewer_accuracy']:.0%}")

        print(f"\n  Cycle {cycle} complete. Waiting 5s before next cycle...")
        print(f"  Press Ctrl+C to stop.\n")
        time.sleep(5)


if __name__ == "__main__":
    main()
