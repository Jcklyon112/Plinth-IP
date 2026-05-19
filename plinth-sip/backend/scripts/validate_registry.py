"""
Validate State GIS Registry
Tests that every state entry in STATE_REGISTRY:
  1. Has required fields (parcel_service_url, field_map, etc.)
  2. ArcGIS endpoint responds (HEAD or small query)
  3. field_map keys are valid internal names

Usage:
    backend\\venv\\Scripts\\python.exe backend\\scripts\\validate_registry.py
"""

import sys
import os
import json
import time
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agents.state_gis_registry import STATE_REGISTRY

REQUIRED_FIELDS = [
    "name", "parcel_source", "parcel_service_url",
    "field_map", "lot_size_unit",
]

REQUIRED_FIELD_MAP_KEYS = ["parcel_id", "address", "lot_size", "zoning_code"]

VALID_INTERNAL_KEYS = {
    "parcel_id", "address", "owner_name", "owner_mail",
    "zoning_code", "lot_size", "lot_units", "use_code", "bld_area", "units",
}


def validate_state(state_abbr: str, cfg: dict) -> list[str]:
    """Return list of issues for this state config."""
    issues = []

    # Check required top-level fields
    for field in REQUIRED_FIELDS:
        if field not in cfg:
            issues.append(f"Missing required field: {field}")

    # Check field_map
    field_map = cfg.get("field_map", {})
    for key in REQUIRED_FIELD_MAP_KEYS:
        if key not in field_map:
            issues.append(f"field_map missing required key: {key}")

    # Check field_map keys are valid
    for key in field_map:
        if key not in VALID_INTERNAL_KEYS:
            issues.append(f"field_map has unknown key: {key}")

    # Check lot_size_unit
    unit = cfg.get("lot_size_unit")
    if unit and unit not in ("sqft", "acres"):
        issues.append(f"Invalid lot_size_unit: {unit} (expected 'sqft' or 'acres')")

    # Test ArcGIS endpoint
    url = cfg.get("parcel_service_url")
    if url:
        try:
            test_url = f"{url}/query?where=1=1&returnCountOnly=true&f=json"
            resp = requests.get(test_url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                count = data.get("count")
                if count is not None:
                    print(f"    Endpoint OK — {count:,} features available")
                elif "error" in data:
                    issues.append(f"ArcGIS error: {data['error'].get('message', 'unknown')}")
                else:
                    print(f"    Endpoint responded but no count returned")
            else:
                issues.append(f"HTTP {resp.status_code} from ArcGIS endpoint")
        except requests.Timeout:
            issues.append("ArcGIS endpoint timed out (15s)")
        except requests.ConnectionError:
            issues.append("ArcGIS endpoint unreachable")
        except Exception as e:
            issues.append(f"ArcGIS test error: {e}")

    return issues


def main():
    print("=" * 60)
    print("Plinth SIP — State GIS Registry Validator")
    print("=" * 60)

    total_states = 0
    passed = 0
    failed = 0
    all_issues = {}

    for state_abbr, cfg in sorted(STATE_REGISTRY.items()):
        total_states += 1
        status = cfg.get("status", "unknown")
        print(f"\n  [{state_abbr}] {cfg.get('name', '???')}  (status: {status})")

        if status == "planned":
            print(f"    Skipping — planned but not implemented")
            continue

        issues = validate_state(state_abbr, cfg)

        if issues:
            failed += 1
            all_issues[state_abbr] = issues
            for issue in issues:
                print(f"    FAIL: {issue}")
        else:
            passed += 1
            print(f"    PASS")

        time.sleep(0.5)  # Be polite to ArcGIS servers

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, {total_states} total")

    if all_issues:
        print(f"\nFailed states:")
        for state, issues in all_issues.items():
            print(f"  {state}: {', '.join(issues)}")
        sys.exit(1)
    else:
        print("\nAll states validated successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()
