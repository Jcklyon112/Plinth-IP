"""
Config resolver — decides how to get a municipality config.

Priority order:
  1. Existing high-confidence config on disk (analyst-verified or extracted)
  2. LangGraph zoning extraction from the real ordinance
  3. Auto-generated config from state defaults (lowest confidence)

The LangGraph extractor is only attempted when:
  - ANTHROPIC_API_KEY is set
  - No high-confidence config already exists
  - The existing config is auto-generated with LOW confidence
"""

import json
import os
import time
from pathlib import Path


CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "configs" / "municipalities"


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _load_config_file(path: Path) -> dict | None:
    """Load a config JSON file if it exists."""
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def _find_config(configs_dir: Path, municipality_id: str, municipality_name: str = "", state_code: str = "") -> dict | None:
    """
    Find a config file by exact ID match, then by fuzzy prefix match.
    Returns the loaded config dict or None.
    """
    # Exact match
    config = _load_config_file(configs_dir / f"{municipality_id}.json")
    if config:
        return config

    # Try prefix match: ny_sag_harbor*
    if municipality_name and state_code:
        prefix = f"{state_code.lower()}_{municipality_name.lower().replace(' ', '_')}"
        for p in configs_dir.glob(f"{prefix}*.json"):
            with open(p) as f:
                return json.load(f)

    return None


def _save_config(config: dict, configs_dir: Path) -> Path:
    """Save config to disk. Returns the path."""
    configs_dir.mkdir(parents=True, exist_ok=True)
    path = configs_dir / f"{config['municipality_id']}.json"
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return path


# ---------------------------------------------------------------------------
# LangGraph extraction (with timeout)
# ---------------------------------------------------------------------------

def _try_langgraph_extraction(
    municipality_name: str,
    state_code: str,
    municipality_id: str,
    county: str,
    timeout_seconds: int = 120,
) -> dict | None:
    """
    Attempt LangGraph zoning extraction. Returns a config dict on success,
    None on failure or timeout.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print(f"  [config] No ANTHROPIC_API_KEY — skipping LangGraph extraction")
        return None

    print(f"  [config] Running LangGraph zoning extraction for {municipality_name}, {state_code}...")
    t0 = time.time()

    try:
        from app.agents.zoning_extractor import extract_zoning_config

        result = extract_zoning_config(
            municipality_name=municipality_name,
            state=state_code,
            county=county,
            municipality_id=municipality_id,
        )

        elapsed = time.time() - t0

        if result.get("error"):
            print(f"  [config] Extraction failed ({elapsed:.1f}s): {result['error']}")
            return None

        config = result.get("config")
        if not config or not config.get("zoning_districts"):
            print(f"  [config] Extraction returned no districts ({elapsed:.1f}s)")
            return None

        confidence = result.get("confidence", 0.0)
        districts_count = result.get("districts_found", 0)
        print(
            f"  [config] Extraction succeeded ({elapsed:.1f}s): "
            f"{districts_count} districts, {confidence:.0%} confidence, "
            f"source: {result.get('source', 'unknown')}"
        )
        return config

    except Exception as e:
        elapsed = time.time() - t0
        print(f"  [config] Extraction exception ({elapsed:.1f}s): {type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------------------
# Auto-config fallback
# ---------------------------------------------------------------------------

def _generate_auto_config(
    municipality_name: str,
    state_code: str,
    municipality_id: str,
    county: str,
    zoning_codes: list[str] | None = None,
) -> dict:
    """Generate a LOW-confidence auto config from state defaults."""
    from app.agents.auto_config import generate_municipality_config

    config = generate_municipality_config(
        municipality_id=municipality_id,
        municipality_name=municipality_name,
        state=state_code.upper(),
        county=county,
        zoning_codes=zoning_codes or [],
        median_lot_sqft=None,
        sewer_override=None,
    )
    return config


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_or_extract_config(
    municipality_name: str,
    state_code: str,
    municipality_id: str,
    county: str = "",
    zoning_codes: list[str] | None = None,
    progress_callback=None,
) -> dict:
    """
    Get the best available municipality config.

    Priority:
      1. Existing HIGH or MEDIUM confidence config on disk → use as-is
      2. LangGraph extraction from real ordinance → save and use
      3. Auto-generated from state defaults → save and use

    Args:
        municipality_name: e.g. "Sag Harbor"
        state_code: e.g. "NY"
        municipality_id: e.g. "ny_sag_harbor_village"
        county: e.g. "Suffolk County"
        zoning_codes: discovered zoning codes from parcel data (improves auto fallback)
        progress_callback: optional fn(status_msg) for UI progress updates

    Returns:
        Config dict ready for scoring.
    """
    def _progress(msg):
        if progress_callback:
            progress_callback(msg)
        print(f"  [config] {msg}")

    # ── Step 1: Check for existing config on disk ──────────────────────
    existing = _find_config(CONFIGS_DIR, municipality_id, municipality_name, state_code)

    # Guard: never reuse a config from a different state
    if existing:
        existing_state = existing.get("state", "").upper()
        if existing_state and existing_state != state_code.upper():
            _progress(f"Config state mismatch ({existing_state} vs {state_code}) — discarding")
            existing = None

    if existing:
        is_auto = existing.get("auto_generated", False)
        confidence_label = existing.get("auto_generated_confidence", "LOW")
        confidence_float = existing.get("data_sources", {}).get("zoning_data", {}).get("confidence", 0)

        # Only trust: analyst-verified configs (not auto_generated at all)
        if not is_auto:
            _progress(f"Using analyst-verified config for {municipality_id}")
            return existing

        # Or LangGraph-extracted with high confidence (>= 0.65)
        if confidence_float >= 0.65:
            _progress(f"Using high-confidence extracted config ({confidence_float:.0%})")
            return existing

        # Everything else — re-run LangGraph to get real data
        _progress(f"Config exists but confidence too low ({confidence_label}, {confidence_float:.0%}) — re-running LangGraph")

    else:
        _progress(f"No config found for {municipality_id} — attempting extraction")

    # ── Step 2: Try LangGraph extraction ───────────────────────────────
    if progress_callback:
        progress_callback("extracting")

    extracted_config = _try_langgraph_extraction(
        municipality_name=municipality_name,
        state_code=state_code,
        municipality_id=municipality_id,
        county=county,
    )

    if extracted_config and extracted_config.get("zoning_districts"):
        # Merge discovered zoning_codes into the extracted config's zoning_code_map
        # so parcels with codes not in the ordinance still map to a district
        if zoning_codes:
            zcm = extracted_config.get("zoning_code_map", {})
            districts = extracted_config.get("zoning_districts", {})
            # Find the first residential district key as fallback
            fallback_key = None
            for dk, dv in districts.items():
                if any(u in (dv.get("use_allowed") or []) for u in ["single_family", "residential"]):
                    fallback_key = dk
                    break
            if not fallback_key and districts:
                fallback_key = next(iter(districts))

            if fallback_key:
                from app.agents.auto_config import classify_zoning_code
                for code in zoning_codes:
                    if code not in zcm:
                        classification = classify_zoning_code(code)
                        if classification == "non_residential":
                            zcm[code] = None
                        else:
                            zcm[code] = fallback_key
                extracted_config["zoning_code_map"] = zcm

        # NY residential code backfill (same as auto_config)
        if state_code.upper() == "NY":
            _backfill_ny_codes(extracted_config)

        # Save for future reuse (overwrites LOW config)
        path = _save_config(extracted_config, CONFIGS_DIR)
        _progress(f"Saved extracted config -> {path.name}")
        return extracted_config

    # ── Step 3: Fall back to auto-generated config ─────────────────────
    _progress(f"Extraction unavailable — generating auto config")

    if existing:
        # We already have a LOW auto config — if we have new zoning_codes,
        # regenerate it with better data. Otherwise reuse existing.
        if zoning_codes and len(zoning_codes) > 1:
            _progress(f"Regenerating auto config with {len(zoning_codes)} discovered codes")
            config = _generate_auto_config(
                municipality_name, state_code, municipality_id, county, zoning_codes
            )
            path = _save_config(config, CONFIGS_DIR)
            _progress(f"Saved upgraded auto config -> {path.name}")
            return config
        else:
            return existing

    # No existing config at all — generate fresh
    config = _generate_auto_config(
        municipality_name, state_code, municipality_id, county, zoning_codes
    )
    path = _save_config(config, CONFIGS_DIR)
    _progress(f"Generated auto config -> {path.name}")
    return config


def _backfill_ny_codes(config: dict):
    """Ensure all NY residential prop class codes map to a residential district."""
    NY_RESIDENTIAL_CODES = [
        "210", "211", "212", "213", "214", "215", "216", "217", "218", "219",
        "220", "221", "222", "223", "224", "225", "226", "227", "228", "229",
        "230", "240", "241", "242", "250", "260", "270", "280", "281", "283",
    ]
    NY_NON_RESIDENTIAL_CODES = [
        "300", "311", "312", "314", "322", "330", "340",
        "400", "411", "421", "432", "449", "464", "480",
        "500", "600", "620", "651", "695",
        "710", "720", "800", "900",
    ]
    zcm = config.get("zoning_code_map", {})
    districts = config.get("zoning_districts", {})

    res_key = None
    for dk, dv in districts.items():
        if any(u in (dv.get("use_allowed") or []) for u in ["single_family", "residential"]):
            res_key = dk
            break
    if not res_key and districts:
        res_key = next(iter(districts))

    if res_key:
        for code in NY_RESIDENTIAL_CODES:
            if code not in zcm:
                zcm[code] = res_key
        for code in NY_NON_RESIDENTIAL_CODES:
            if code not in zcm:
                zcm[code] = None
        config["zoning_code_map"] = zcm


# ---------------------------------------------------------------------------
# Backwards-compatible alias
# ---------------------------------------------------------------------------

def get_or_generate_config(municipality_name: str, state_code: str, municipality_id: str, county: str = "") -> dict:
    """Backwards-compatible wrapper — delegates to get_or_extract_config."""
    return get_or_extract_config(municipality_name, state_code, municipality_id, county)
