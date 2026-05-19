"""
Phase 5 System Test
Tests each component of the auto-scan pipeline individually.

Usage:
    python scripts/test_phase5.py                   # run all tests
    python scripts/test_phase5.py --test resolver   # test only resolver
    python scripts/test_phase5.py --test fetcher    # test only fetcher (makes real API call)
    python scripts/test_phase5.py --test config     # test only config gen

No DB required for most tests.
"""

import sys
import os
import json
import argparse

# Fix Unicode output on Windows console (cp1252 can't handle ✓/✗)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_resolver():
    print("\n[1] Municipality Resolver")
    print("-" * 40)
    from app.agents.municipality_resolver import resolve_municipality

    cases = [
        "Burlington, VT",
        "14 Main St, Acton MA",
        "05401",
        "Concord, NH",
    ]
    passed = 0
    for case in cases:
        result = resolve_municipality(case)
        if result:
            print(f"  ✓ '{case}' → {result['municipality_name']}, {result['state']} ({result['municipality_id']})")
            passed += 1
        else:
            print(f"  ✗ '{case}' → FAILED")

    print(f"  Passed: {passed}/{len(cases)}")
    return passed == len(cases)


def test_state_registry():
    print("\n[2] State GIS Registry")
    print("-" * 40)
    from app.agents.state_gis_registry import list_supported_states, get_state_config

    states = list_supported_states()
    print(f"  States in registry: {len(states)}")
    for s in states:
        print(f"    {s['state']} — {s['name']} [{s['status']}]")

    # Check MA is production
    ma = get_state_config("MA")
    assert ma is not None, "MA config missing"
    assert ma.get("status") == "production", "MA should be production"
    assert "field_map" in ma, "MA should have field_map"

    # Check a beta state
    vt = get_state_config("VT")
    assert vt is not None, "VT config missing"
    assert "parcel_service_url" in vt, "VT should have service URL"

    print("  ✓ Registry structure valid")
    return True


def test_config_generator():
    print("\n[3] Auto-Config Generator")
    print("-" * 40)
    from app.agents.auto_config import generate_municipality_config, classify_zoning_code

    # Test code classification
    test_codes = {
        "R-2": "low_density_residential",
        "VR": "dense_residential",
        "R-8": "rural_residential",
        "B": "non_residential",
        "IND": "non_residential",
    }
    code_ok = True
    for code, expected in test_codes.items():
        result = classify_zoning_code(code)
        ok = result == expected
        print(f"  {'✓' if ok else '✗'} classify '{code}' → '{result}' (expected '{expected}')")
        if not ok:
            code_ok = False

    # Test config generation
    config = generate_municipality_config(
        municipality_id="vt_burlington",
        municipality_name="Burlington",
        state="VT",
        county="Chittenden",
        zoning_codes=["R-1", "R-2", "R-3", "VR", "COMM", "IND"],
        median_lot_sqft=8500,
    )

    assert config["municipality_id"] == "vt_burlington"
    assert config["state"] == "VT"
    assert "zoning_districts" in config
    assert len(config["zoning_districts"]) > 0
    assert config["auto_generated"] == True

    # Non-residential codes should be excluded from districts
    for code in ["COMM", "IND"]:
        code_key = code.replace("-", "_")
        assert code_key not in config["zoning_districts"], f"{code} should not be in districts"
        assert config["zoning_code_map"].get(code) is None, f"{code} should map to null"

    print(f"\n  ✓ Config generated with {len(config['zoning_districts'])} districts")
    print(f"  Districts: {list(config['zoning_districts'].keys())}")
    print(f"  Zoning code map: {config['zoning_code_map']}")
    return code_ok


def test_gis_fetcher_live(municipality: str = "Acton", state: str = "MA"):
    """Makes a real API call — skip if no internet connection."""
    print(f"\n[4] GIS Fetcher (live API call: {municipality}, {state})")
    print("-" * 40)
    print("  Making real ArcGIS REST API call — this requires internet access.")

    from app.agents.state_gis_registry import get_state_config
    from app.agents.gis_fetcher import _discover_town_filter

    state_cfg = get_state_config(state)
    if not state_cfg:
        print(f"  ✗ State {state} not in registry")
        return False

    # Just test the filter discovery (lighter than full fetch)
    try:
        where, discovered_id = _discover_town_filter(state_cfg, municipality)
        print(f"  ✓ Discovered filter: WHERE {where}")
        return True
    except RuntimeError as e:
        print(f"  ✗ Filter discovery failed: {e}")
        return False
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Phase 5 system tests")
    parser.add_argument("--test", choices=["resolver", "registry", "config", "fetcher", "all"],
                        default="all")
    parser.add_argument("--municipality", default="Acton",
                        help="Municipality for live fetcher test")
    parser.add_argument("--state", default="MA",
                        help="State for live fetcher test")
    args = parser.parse_args()

    print("Plinth SIP — Phase 5 System Tests")
    print("=" * 40)

    results = {}

    if args.test in ("all", "resolver"):
        results["resolver"] = test_resolver()

    if args.test in ("all", "registry"):
        results["registry"] = test_state_registry()

    if args.test in ("all", "config"):
        results["config"] = test_config_generator()

    if args.test in ("fetcher",):
        results["fetcher"] = test_gis_fetcher_live(args.municipality, args.state)

    print("\n" + "=" * 40)
    print("Test Summary:")
    all_passed = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {status}  {name}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("  All tests passed. Phase 5 components are ready.")
    else:
        print("  Some tests failed. Check output above.")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
