"""
Stateless Shape Analysis Pipeline

When a user draws a polygon on the map:
1. Identify state from polygon centroid
2. Query ArcGIS REST API with polygon spatial filter (no DB)
3. Normalize raw features to Plinth parcel schema
4. Load/generate municipality config from files (no DB)
5. Score all parcels in parallel in memory (no DB writes)
6. Optionally generate AI explanations
7. Return results via in-memory job store

The database is NOT used for parcel data in this flow.
"""

import json
import os
import traceback
from typing import TypedDict

from shapely.geometry import shape as shapely_shape


# ---------------------------------------------------------------------------
# In-memory job store (MVP)
# ---------------------------------------------------------------------------

ANALYSIS_JOBS: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Status labels
# ---------------------------------------------------------------------------

STATUS_LABELS = {
    "resolving": "Identifying location...",
    "fetching": "Fetching parcels from GIS...",
    "extracting": "Extracting zoning rules from ordinance...",
    "configuring": "Loading zoning rules...",
    "overlays": "Checking environmental & regulatory overlays...",
    "scoring": "Scoring parcels...",
    "explaining": "Generating AI explanations...",
    "complete": "Analysis complete",
    "error": "Analysis failed",
}


# ---------------------------------------------------------------------------
# Main pipeline (runs as background task)
# ---------------------------------------------------------------------------

def run_shape_analysis(analysis_id: str, polygon_geojson: dict, municipality_id: str = ""):
    """
    Stateless analysis pipeline. Fetches parcels from ArcGIS, scores in memory.
    No database writes for parcel data.
    """
    job = {
        "status": "resolving",
        "progress": 5,
        "error": "",
        "parcel_count": 0,
        "tier_counts": {},
        "parcels": [],
        "explanations": {},
        "summary": "",
        "config_upgrade_notes": {},
        "municipality_name": "",
        "state": "",
    }
    ANALYSIS_JOBS[analysis_id] = job

    def _update(status: str, progress: int, **kwargs):
        job["status"] = status
        job["progress"] = progress
        for k, v in kwargs.items():
            job[k] = v

    try:
        # ── Step 1: Resolve location from polygon centroid ──────────────
        polygon_shape = shapely_shape(polygon_geojson)
        centroid = polygon_shape.centroid
        lon, lat = centroid.x, centroid.y

        from app.agents.municipality_resolver import reverse_geocode
        print(f"[analysis] Centroid: lat={lat}, lon={lon}")
        resolved = reverse_geocode(lat, lon)
        print(f"[analysis] Reverse geocode result: {resolved}")

        if not resolved:
            _update("error", 100, error="Could not identify location. Make sure your polygon is within the United States.")
            return

        state_code = resolved["state"]
        municipality_name = resolved["municipality_name"]
        county = resolved.get("county", "")
        muni_id = resolved["municipality_id"]

        _update("fetching", 15, municipality_name=municipality_name, state=state_code)
        print(f"[analysis] Resolved: {municipality_name}, {state_code} (county: {county})")

        # ── Step 2: Fetch parcels from ArcGIS with spatial filter ──────
        from app.agents.gis_fetcher import fetch_parcels_in_polygon, normalize_arcgis_feature
        from app.agents.state_gis_registry import get_state_config

        # Determine the registry key: try county-specific first, then state-level
        registry_key = state_code
        if not get_state_config(state_code) and county:
            county_slug = county.upper().replace(" ", "_").replace("'", "")
            county_key = f"{state_code}_{county_slug}"
            if get_state_config(county_key):
                registry_key = county_key
                print(f"[analysis] Using county-specific registry: {registry_key}")
        elif get_state_config(state_code):
            pass  # statewide endpoint exists
        elif county:
            # State key doesn't exist — try county variant
            county_slug = county.upper().replace(" ", "_").replace("'", "")
            county_key = f"{state_code}_{county_slug}"
            if get_state_config(county_key):
                registry_key = county_key
                print(f"[analysis] Using county-specific registry: {registry_key}")

        # Outer retry guard — fetch_parcels_in_polygon already retries each
        # HTTP call internally, but if the upstream GIS server is genuinely
        # flapping we give the whole request one more chance after a longer pause.
        features = []
        last_fetch_error: Exception | None = None
        for outer_attempt in range(2):
            try:
                features = fetch_parcels_in_polygon(registry_key, polygon_geojson)
                last_fetch_error = None
                break
            except Exception as e:
                last_fetch_error = e
                print(f"[analysis] fetch attempt {outer_attempt + 1} failed: {type(e).__name__}: {e}")
                if outer_attempt == 0:
                    import time
                    time.sleep(5)

        if last_fetch_error is not None:
            err_str = str(last_fetch_error)
            friendly = (
                f"The {state_code} parcel GIS server is currently unresponsive "
                f"(connection was dropped). This usually clears up within a minute — "
                f"please draw the area again. Underlying error: "
                f"{type(last_fetch_error).__name__}: {err_str[:200]}"
            )
            _update("error", 100, error=friendly)
            return

        if not features:
            _update("error", 100, error="No parcel data found in this area. The state GIS server returned 0 features.")
            return

        print(f"[analysis] Fetched {len(features)} raw features from {state_code} ArcGIS")
        # Surface count to the UI as soon as we know it — long before scoring.
        job["parcel_count"] = len(features)

        # ── Step 3: Normalize features ─────────────────────────────────
        _update("configuring", 35)
        normalized = []
        for f in features:
            p = normalize_arcgis_feature(f, registry_key, muni_id)
            if p and p.get("parcel_id"):
                # Attach geometry for frontend rendering
                p["geometry"] = f.get("geometry")
                normalized.append(p)

        if not normalized:
            _update("error", 100, error="Fetched features but none had valid parcel IDs after normalization.")
            return

        print(f"[analysis] Normalized {len(normalized)} parcels")
        job["parcel_count"] = len(normalized)

        # Use county from parcel data if reverse geocode missed it
        if not county:
            for p in normalized:
                if p.get("county_name"):
                    county = p["county_name"]
                    print(f"[analysis] County from parcel data: {county}")
                    break

        # Use muni_name from parcel data if available (more accurate than geocoder)
        for p in normalized:
            if p.get("muni_name"):
                parcel_muni = p["muni_name"]
                if parcel_muni and parcel_muni != municipality_name:
                    print(f"[analysis] Municipality from parcel data: {parcel_muni} (geocoder said: {municipality_name})")
                    municipality_name = parcel_muni
                break

        # ── Step 4: Load or extract config ────────────────────────────
        # Discover zoning codes from parcel data to improve config quality
        zoning_codes = list({
            str(p.get("zoning_code", "")).strip()
            for p in normalized
            if p.get("zoning_code") and str(p["zoning_code"]).strip()
        })

        from app.agents.config_resolver import get_or_extract_config

        def _config_progress(msg):
            if msg == "extracting":
                _update("extracting", 38)
            else:
                print(f"[analysis] {msg}")

        _update("configuring", 35)
        config = get_or_extract_config(
            municipality_name, state_code, muni_id, county,
            zoning_codes=zoning_codes,
            progress_callback=_config_progress,
        )

        # Determine config quality label for the UI
        is_auto = config.get("auto_generated", False)
        confidence_label = config.get("auto_generated_confidence", "")
        source_type = config.get("data_sources", {}).get("zoning_data", {}).get("format", "")

        if not is_auto:
            config_note = "analyst-verified"
        elif source_type == "ordinance_extraction":
            config_note = f"extracted ({confidence_label})"
        else:
            config_note = f"auto-generated ({confidence_label})"

        print(f"[analysis] Config loaded ({config_note}): {len(config.get('zoning_districts', {}))} districts")

        # ── Step 4b: Live spatial overlay intersection ─────────────────
        # Fetches each registered overlay layer (FEMA flood, MA wetlands,
        # NHESP habitat, ACEC, 21E sites, etc.) ONCE for the user polygon,
        # then runs per-parcel intersection locally with shapely.
        _update("overlays", 45)
        try:
            from app.agents.overlay_service import (
                fetch_overlays_for_polygon,
                annotate_parcels_with_overlays,
            )
            overlay_features = fetch_overlays_for_polygon(polygon_geojson, state_code)
            annotate_parcels_with_overlays(normalized, overlay_features)
            overlay_layers_loaded = sum(1 for gdf in overlay_features.values() if gdf is not None and not gdf.empty)
            total_hits = sum(len(p.get("overlay_hits") or []) for p in normalized)
            print(f"[analysis] Overlays: {overlay_layers_loaded} non-empty layers, {total_hits} parcel-overlay intersections")
        except Exception as e:
            # Overlay enrichment is best-effort. Scoring continues with whatever
            # constraints_flags were pre-tagged during ingestion.
            print(f"[analysis] Overlay enrichment failed: {type(e).__name__}: {e}")
            traceback.print_exc()

        # ── Step 4c: LiDAR slope stats per parcel ──────────────────────
        # Server-side computeStatisticsHistograms against the state DEM
        # ImageServer; one HTTP call per parcel, parallelized.
        try:
            from app.agents.elevation_service import annotate_parcels_with_slope, ELEVATION_SERVICES
            if state_code.upper() in ELEVATION_SERVICES:
                ok = annotate_parcels_with_slope(normalized, state_code)
                print(f"[analysis] Slope: computed for {ok}/{len(normalized)} parcels")
            else:
                print(f"[analysis] Slope: no DEM service registered for {state_code}, skipping")
        except Exception as e:
            print(f"[analysis] Slope enrichment failed: {type(e).__name__}: {e}")
            traceback.print_exc()

        # ── Step 4d: SSURGO soil septic suitability per parcel ─────────
        # USDA Soil Data Access — batched query of dominant-component
        # septic-tank-absorption rating ("Not limited" / "Somewhat limited"
        # / "Very limited"). Drives the septic_capacity rule for non-sewered
        # parcels; falls back to lot-size heuristic when no rating returns.
        try:
            from app.agents.soil_service import annotate_parcels_with_soil
            ok = annotate_parcels_with_soil(normalized)
            print(f"[analysis] Soil: SSURGO septic class for {ok}/{len(normalized)} parcels")
        except Exception as e:
            print(f"[analysis] Soil enrichment failed: {type(e).__name__}: {e}")
            traceback.print_exc()

        # ── Step 5: Score parcels in parallel ──────────────────────────
        _update("scoring", 50, parcel_count=len(normalized))

        # Standard Plinth templates — both units share the same 15x35 ft footprint.
        # These match the seeded DB templates (plinth_studio, plinth_1br).
        PLINTH_TEMPLATES = [
            {
                "template_id": "plinth_studio",
                "template_name": "Plinth Studio",
                "footprint_area_sqft": 525,
                "bedrooms": 0,
                "active_status": True,
            },
            {
                "template_id": "plinth_1br",
                "template_name": "Plinth 1BR",
                "footprint_area_sqft": 525,
                "bedrooms": 1,
                "active_status": True,
            },
        ]

        from app.engine.parallel_scorer import score_parcels_parallel
        scored = score_parcels_parallel(normalized, config, templates=PLINTH_TEMPLATES, max_workers=8)

        # Compute tier counts
        tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        for p in scored:
            t = p.get("tier")
            if t in tier_counts:
                tier_counts[t] += 1

        # Build response parcels — include rule results and zoning label for UI
        response_parcels = []
        for p in scored:
            # Serialize rule_results (RuleResult objects → dicts)
            raw_rules = p.get("rule_results") or {}
            rule_list = []
            if isinstance(raw_rules, dict):
                for rule_id, rr in raw_rules.items():
                    try:
                        rule_list.append({
                            "rule_id": rr.rule_id,
                            "rule_category": rr.rule_category,
                            "result": rr.result,
                            "explanation": rr.explanation,
                            "confidence": rr.confidence,
                        })
                    except Exception:
                        pass

            response_parcels.append({
                "parcel_id": p.get("parcel_id"),
                "municipality_id": p.get("municipality_id"),
                "address": p.get("address"),
                "owner_name": p.get("owner_name"),
                "zoning_code": p.get("zoning_code"),
                "zoning_district": p.get("zoning_district"),
                "zoning_district_label": p.get("zoning_district_label"),
                "lot_area_sqft": p.get("lot_area_sqft"),
                "land_use_type": p.get("land_use_type"),
                "score": p.get("score"),
                "tier": p.get("tier"),
                "confidence": p.get("confidence"),
                "score_breakdown": p.get("score_breakdown"),
                "blockers": p.get("blockers"),
                "rule_results": rule_list,
                "template_fits": p.get("template_fits"),
                "constraints_flags": p.get("constraints_flags") or [],
                "overlay_hits": p.get("overlay_hits") or [],
                "year_built": p.get("year_built"),
                "slope_stats": p.get("slope_stats"),
                "soil_septic_class": p.get("soil_septic_class"),
            })

        _update("explaining", 80, parcels=response_parcels, tier_counts=tier_counts)
        print(f"[analysis] Scored: T1={tier_counts[1]} T2={tier_counts[2]} T3={tier_counts[3]} T4={tier_counts[4]}")

        # ── Step 6: AI explanations (optional) ─────────────────────────
        explanations = {}
        summary = (
            f"Analyzed {len(scored)} parcels in {municipality_name}, {state_code}. "
            f"Tier 1: {tier_counts[1]}, Tier 2: {tier_counts[2]}, "
            f"Tier 3: {tier_counts[3]}, Tier 4: {tier_counts[4]}. "
            f"Config: {config_note} with {len(config.get('zoning_districts', {}))} zoning districts."
        )

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key and scored:
            try:
                from langchain_anthropic import ChatAnthropic

                # Per-parcel explanations (batched, haiku for speed)
                llm_fast = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0.0, max_tokens=4096, api_key=api_key)
                BATCH = 20
                for i in range(0, len(scored), BATCH):
                    batch = scored[i:i + BATCH]
                    prompt_parts = []
                    for sp in batch:
                        rules_str = ""
                        for rr in (sp.get("rule_results") or []):
                            if isinstance(rr, dict):
                                rules_str += f"  {rr.get('rule_id','?')}: {rr.get('result','?')}\n"
                        prompt_parts.append(
                            f"PARCEL [{sp['parcel_id']}]: {sp.get('address','N/A')}, "
                            f"zone={sp.get('zoning_code','?')}, lot={sp.get('lot_area_sqft','?')} sqft, "
                            f"score={sp.get('score','?')}, tier={sp.get('tier','?')}\n"
                            f"Rules:\n{rules_str}"
                        )
                    prompt = (
                        "For each parcel, write 2 sentences on ADU feasibility. "
                        "Format: PARCEL_ID: explanation\n\n" + "\n---\n".join(prompt_parts)
                    )
                    try:
                        resp = llm_fast.invoke(prompt)
                        text = resp.content if hasattr(resp, "content") else str(resp)
                        for line in text.strip().split("\n"):
                            for sp in batch:
                                pid = sp["parcel_id"]
                                if pid in line:
                                    after = line[line.find(pid) + len(pid):].lstrip("]:").lstrip(" ")
                                    if after:
                                        explanations[pid] = after
                                    break
                    except Exception:
                        pass

                # Summary (sonnet)
                llm_summary = ChatAnthropic(model="claude-sonnet-4-6", temperature=0.0, max_tokens=1024, api_key=api_key)
                blocker_counts: dict[str, int] = {}
                for sp in scored:
                    for b in (sp.get("blockers") or []):
                        rid = b.get("rule_id", "unknown") if isinstance(b, dict) else "unknown"
                        blocker_counts[rid] = blocker_counts.get(rid, 0) + 1
                common_blockers = sorted(blocker_counts.items(), key=lambda x: -x[1])[:3]
                top = sorted([p for p in scored if p.get("score")], key=lambda x: x["score"], reverse=True)[:5]
                top_addrs = [p.get("address", p["parcel_id"]) for p in top]

                try:
                    resp = llm_summary.invoke(
                        f"Write a 3-4 sentence summary of ADU feasibility analysis.\n"
                        f"Stats: {len(scored)} parcels in {municipality_name}, {state_code}.\n"
                        f"T1:{tier_counts[1]} T2:{tier_counts[2]} T3:{tier_counts[3]} T4:{tier_counts[4]}.\n"
                        f"Common blockers: {', '.join(f'{r} ({n})' for r, n in common_blockers) or 'none'}.\n"
                        f"Top opportunities: {', '.join(top_addrs) or 'none'}.\n"
                        f"Be specific and actionable."
                    )
                    summary = resp.content if hasattr(resp, "content") else str(resp)
                except Exception:
                    pass

            except Exception:
                pass

        # ── Step 7: Build GeoJSON features for frontend map ────────────
        geojson_features = []
        for i, p in enumerate(scored):
            geom = p.get("geometry") or p.get("geometry_geojson")
            if geom:
                geojson_features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": response_parcels[i] if i < len(response_parcels) else {},
                })

        _update("complete", 100,
                parcels=response_parcels,
                tier_counts=tier_counts,
                explanations=explanations,
                summary=summary,
                geojson_features=geojson_features,
                config_upgrade_notes={muni_id: config_note})

        print(f"[analysis] Complete: {len(scored)} parcels analyzed")

    except Exception as e:
        _update("error", 100, error=str(e)[:500])
        print(f"[analysis] Error: {e}")
        traceback.print_exc()
