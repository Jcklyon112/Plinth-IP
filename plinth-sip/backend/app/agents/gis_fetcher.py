"""
Generic ArcGIS REST Parcel Fetcher — uses httpx for reliable HTTP.
Fetches parcel geometry + attributes from any state's ArcGIS endpoint
using the config from state_gis_registry.

Supports:
  - Automatic town/municipality filter discovery
  - **Parallel** paginated GeoJSON geometry + attribute fetch
  - Returns a GeoPandas GeoDataFrame in WGS84
  - Local file cache (data/cache/) for fast re-scans

Usage (standalone):
    python -m app.agents.gis_fetcher "Burlington" VT
    python -m app.agents.gis_fetcher "Acton" MA
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError

import geopandas as gpd
from shapely.geometry import shape

from app.agents.state_gis_registry import get_state_config, STATE_REGISTRY


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_BACKOFF_SCHEDULE = [1, 2, 4, 8, 15, 25, 40, 60]


def _fetch_json(url: str, timeout: int = 60, max_retries: int = 5) -> dict:
    """GET JSON with backoff retry on transient connection errors."""
    import httpx
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, http2=False) as client:
                resp = client.get(url, headers=_BROWSER_HEADERS)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(_BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)])
    raise last_error if last_error else RuntimeError("fetch_json failed")


def _post_json(url: str, params: dict, timeout: int = 120, max_retries: int = 5) -> dict:
    """POST form-encoded query with backoff retry — more reliable than GET for some ArcGIS servers."""
    import httpx
    last_error: Exception | None = None
    headers = {**_BROWSER_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, http2=False) as client:
                resp = client.post(url, data=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(_BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)])
    raise last_error if last_error else RuntimeError("post_json failed")


# ---------------------------------------------------------------------------
# Local cache helpers
# ---------------------------------------------------------------------------

CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "cache"
CACHE_MAX_AGE_DAYS = 7


def _cache_key(state_cfg: dict, municipality_name: str) -> str:
    """Deterministic cache key for a municipality fetch."""
    import re
    clean = re.sub(r'\s+(town|city|village|borough|township|CDP)$', '',
                   municipality_name, flags=re.IGNORECASE).strip()
    slug = clean.lower().replace(" ", "_").replace("'", "")
    state = state_cfg.get("name", "unknown").lower().replace(" ", "_")
    return f"{state}_{slug}"


def _find_cached(cache_key: str) -> Path | None:
    """Find a cached GeoJSON file younger than CACHE_MAX_AGE_DAYS."""
    if not CACHE_DIR.exists():
        return None
    cutoff = datetime.now() - timedelta(days=CACHE_MAX_AGE_DAYS)
    candidates = sorted(CACHE_DIR.glob(f"{cache_key}_*.geojson"), reverse=True)
    for p in candidates:
        # Extract date from filename: key_YYYYMMDD.geojson
        try:
            date_str = p.stem.split("_")[-1]
            file_date = datetime.strptime(date_str, "%Y%m%d")
            if file_date >= cutoff:
                return p
        except (ValueError, IndexError):
            continue
    return None


def _save_cache(cache_key: str, features: list[dict]) -> Path:
    """Save raw GeoJSON features to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    path = CACHE_DIR / f"{cache_key}_{date_str}.geojson"
    fc = {"type": "FeatureCollection", "features": features}
    with open(path, "w") as f:
        json.dump(fc, f)
    print(f"  Cached {len(features)} features -> {path.name}")
    return path


def _load_cache(path: Path) -> list[dict]:
    """Load features from a cached GeoJSON file."""
    with open(path) as f:
        fc = json.load(f)
    features = fc.get("features", [])
    print(f"  Loaded {len(features)} features from cache: {path.name}")
    return features


# ---------------------------------------------------------------------------
# Town ID discovery
# ---------------------------------------------------------------------------

def _discover_town_filter(state_cfg: dict, municipality_name: str) -> tuple[str, str]:
    """
    Returns (where_clause, discovered_id_or_name).

    For numeric town IDs (MA): queries the service to find the numeric ID
    by searching the CITY field, then returns WHERE TOWN_ID=<id>.

    For string town IDs (NH, VT, etc.): returns WHERE TOWN='<name>' directly.
    """
    service_url = state_cfg["parcel_service_url"]
    town_field = state_cfg["town_id_field"]
    id_type = state_cfg.get("town_id_type", "string")
    poly_field = state_cfg.get("poly_type_field")
    poly_value = state_cfg.get("poly_type_fee_value")

    import re
    clean_name = re.sub(r'\s+(town|city|village|borough|township|CDP)$', '',
                        municipality_name, flags=re.IGNORECASE).strip()

    poly_filter = ""
    if poly_field and poly_value:
        poly_filter = f" AND {poly_field}='{poly_value}'"

    if id_type == "numeric":
        city_fields = ["CITY", "TOWN", "MUNI_NAME", "MUNICIPALITY"]
        town_upper = clean_name.upper()

        for city_field in city_fields:
            params = urlencode({
                "where": f"UPPER({city_field})='{town_upper}'{poly_filter}",
                "outFields": f"{town_field},{city_field}",
                "returnGeometry": "false",
                "resultRecordCount": 1,
                "f": "json",
            })
            url = f"{service_url}/query?{params}"
            try:
                data = _fetch_json(url)
                features = data.get("features", [])
                if features:
                    attrs = features[0].get("attributes", {})
                    numeric_id = attrs.get(town_field)
                    if numeric_id is not None:
                        print(f"  Discovered {town_field}={numeric_id} for '{clean_name}' via {city_field} field")
                        where = f"{town_field}={numeric_id}{poly_filter}"
                        return where, str(numeric_id)
            except Exception:
                continue

        params = urlencode({
            "where": f"UPPER({town_field})='{town_upper}'",
            "outFields": town_field,
            "returnGeometry": "false",
            "resultRecordCount": 1,
            "f": "json",
        })
        url = f"{service_url}/query?{params}"
        try:
            data = _fetch_json(url)
            features = data.get("features", [])
            if features:
                attrs = features[0].get("attributes", {})
                val = attrs.get(town_field)
                if val is not None:
                    print(f"  Discovered {town_field}={val} for '{clean_name}'")
                    where = f"{town_field}={val}{poly_filter}"
                    return where, str(val)
        except Exception:
            pass

        raise RuntimeError(
            f"Could not discover numeric {town_field} for '{clean_name}' "
            f"in {state_cfg.get('name', 'unknown state')}. "
            "Check that the municipality name matches the GIS data exactly."
        )

    else:
        # String match — try exact case first (some servers don't support UPPER)
        query_url = f"{service_url}/query"

        case_variants = [clean_name, clean_name.upper(), clean_name.title()]
        seen = []
        for variant in case_variants:
            if variant in seen:
                continue
            seen.append(variant)
            try:
                data = _post_json(query_url, {
                    "where": f"{town_field}='{variant}'{poly_filter}",
                    "outFields": town_field,
                    "returnGeometry": "false",
                    "resultRecordCount": 1,
                    "f": "json",
                })
                if data.get("features"):
                    actual_value = data["features"][0].get("attributes", {}).get(town_field, variant)
                    print(f"  Confirmed town filter: {town_field}='{actual_value}'")
                    where = f"{town_field}='{actual_value}'{poly_filter}"
                    return where, actual_value
            except Exception:
                continue

        # Fallback: try UPPER()
        town_upper = clean_name.upper()
        try:
            data = _post_json(query_url, {
                "where": f"UPPER({town_field})='{town_upper}'{poly_filter}",
                "outFields": town_field,
                "returnGeometry": "false",
                "resultRecordCount": 1,
                "f": "json",
            })
            if data.get("features"):
                print(f"  Confirmed town filter via UPPER: {town_field}='{town_upper}'")
                where = f"UPPER({town_field})='{town_upper}'{poly_filter}"
                return where, town_upper
        except Exception:
            pass

        where = f"{town_field}='{clean_name}'{poly_filter}"
        print(f"  Using unconfirmed filter: {where}")
        return where, clean_name


# ---------------------------------------------------------------------------
# Single-page fetch (used by both sequential and parallel paths)
# ---------------------------------------------------------------------------

def _fetch_one_page(
    query_url: str,
    where_clause: str,
    out_fields_str: str,
    offset: int,
    page_size: int,
    page_num: int,
) -> tuple[int, list[dict], bool]:
    """
    Fetch a single page from ArcGIS REST. Returns (page_num, features, exceeded).
    Retries up to 3 times on failure.
    """
    query_params = {
        "where": where_clause,
        "outFields": out_fields_str,
        "returnGeometry": "true",
        "outSR": "4326",
        "resultOffset": str(offset),
        "resultRecordCount": str(page_size),
        "f": "geojson",
    }

    data = None
    for attempt in range(3):
        try:
            data = _post_json(query_url, query_params, timeout=120)
            break
        except (HTTPError, URLError, OSError, Exception) as e:
            if attempt < 2:
                wait = 3 * (attempt + 1)
                time.sleep(wait)
            else:
                raise RuntimeError(f"Fetch error on page {page_num} after 3 attempts: {e}")

    if data is None:
        return page_num, [], False

    if data.get("type") == "FeatureCollection":
        features = data.get("features", [])
    elif "error" in data:
        raise RuntimeError(f"ArcGIS API error on page {page_num}: {data['error']}")
    else:
        features = _arcgis_json_to_features(data)

    exceeded = data.get("exceededTransferLimit", False)
    return page_num, features, exceeded


# ---------------------------------------------------------------------------
# Core fetch — parallel when possible
# ---------------------------------------------------------------------------

def fetch_parcels_geojson(
    state_cfg: dict,
    municipality_name: str,
    page_size: int = 1000,
    max_pages: int = 50,
    progress_callback=None,
    force_refresh: bool = False,
) -> list[dict]:
    """
    Fetch all parcel features from an ArcGIS REST endpoint.

    Returns a list of GeoJSON Feature dicts.
    Uses parallel page fetching when total count is known.
    Results are cached locally for 7 days.
    """
    service_url = state_cfg["parcel_service_url"]

    # Check cache first (unless force_refresh)
    ck = _cache_key(state_cfg, municipality_name)
    if not force_refresh:
        cached_path = _find_cached(ck)
        if cached_path:
            return _load_cache(cached_path)

    # Step 1: Discover the town filter
    print(f"  Discovering municipality filter for '{municipality_name}'...")
    try:
        where_clause, discovered_id = _discover_town_filter(state_cfg, municipality_name)
    except RuntimeError as e:
        raise RuntimeError(f"Filter discovery failed: {e}")

    print(f"  Filter: WHERE {where_clause}")

    # Step 2: Get total count
    total_count = None
    try:
        count_data = _post_json(f"{service_url}/query", {
            "where": where_clause,
            "returnCountOnly": "true",
            "f": "json",
        })
        total_count = count_data.get("count", None)
        if total_count is not None:
            print(f"  Total parcels available: {total_count}")
    except Exception:
        print("  Could not determine total count — will fetch sequentially.")

    query_url = f"{service_url}/query"
    field_map = state_cfg.get("field_map", {})
    out_fields = list(set(field_map.values())) if field_map else ["*"]
    out_fields_str = ",".join(out_fields) if out_fields else "*"

    # Step 3: Fetch pages — parallel if we know total_count, sequential otherwise
    if total_count is not None and total_count > page_size:
        all_features = _fetch_parallel(
            query_url, where_clause, out_fields_str,
            page_size, total_count, max_pages, progress_callback,
        )
    else:
        all_features = _fetch_sequential(
            query_url, where_clause, out_fields_str,
            page_size, max_pages, progress_callback,
        )

    if not all_features:
        raise RuntimeError(
            f"No parcels returned for '{municipality_name}' in {state_cfg.get('name')}. "
            "Check that the municipality name is correct."
        )

    print(f"  Total features fetched: {len(all_features)}")

    # Save to cache
    _save_cache(ck, all_features)

    return all_features


def _fetch_parallel(
    query_url: str,
    where_clause: str,
    out_fields_str: str,
    page_size: int,
    total_count: int,
    max_pages: int,
    progress_callback=None,
) -> list[dict]:
    """
    Fetch all pages in parallel using ThreadPoolExecutor.
    For a 15,000-parcel municipality (15 pages): ~5-8s vs 45s+ sequential.
    """
    num_pages = min((total_count + page_size - 1) // page_size, max_pages)
    print(f"  Parallel fetch: {num_pages} pages × {page_size} records ({total_count} total)")

    # Use up to 6 threads — enough parallelism without hammering the server
    max_workers = min(6, num_pages)
    all_features = []
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for page_num in range(1, num_pages + 1):
            offset = (page_num - 1) * page_size
            future = executor.submit(
                _fetch_one_page,
                query_url, where_clause, out_fields_str,
                offset, page_size, page_num,
            )
            futures[future] = page_num

        for future in as_completed(futures):
            page_num = futures[future]
            try:
                pn, features, exceeded = future.result()
                all_features.extend(features)
                completed += 1
                if progress_callback:
                    progress_callback(completed, len(all_features), total_count)
                else:
                    print(f"    Page {pn}: {len(features)} features (total: {len(all_features)}, {completed}/{num_pages} done)")
            except Exception as e:
                print(f"    Page {page_num} failed: {e}")

    return all_features


def _fetch_sequential(
    query_url: str,
    where_clause: str,
    out_fields_str: str,
    page_size: int,
    max_pages: int,
    progress_callback=None,
) -> list[dict]:
    """
    Sequential fetch with 0.3s delay between pages.
    Used when total_count is unknown (must check exceededTransferLimit).
    """
    all_features = []
    offset = 0

    for page_num in range(1, max_pages + 1):
        if progress_callback:
            progress_callback(page_num, len(all_features), None)
        else:
            print(f"  Fetching page {page_num} (offset {offset}, collected {len(all_features)})...")

        try:
            pn, features, exceeded = _fetch_one_page(
                query_url, where_clause, out_fields_str,
                offset, page_size, page_num,
            )
        except RuntimeError as e:
            print(f"    Error: {e}")
            break

        if not features:
            print(f"  No more features at offset {offset}. Done.")
            break

        all_features.extend(features)
        print(f"    Got {len(features)} features (total: {len(all_features)})")

        if not exceeded and len(features) < page_size:
            break

        offset += page_size
        time.sleep(0.3)  # Shorter sleep for sequential — parallel handles its own pacing

    return all_features


def _arcgis_json_to_features(data: dict) -> list[dict]:
    """Convert ArcGIS JSON format to GeoJSON features as fallback."""
    from shapely.geometry import Polygon, MultiPolygon
    from shapely.ops import unary_union

    features = []
    for f in data.get("features", []):
        attrs = f.get("attributes", {})
        arcgis_geom = f.get("geometry")
        if not arcgis_geom:
            continue

        try:
            rings = arcgis_geom.get("rings", [])
            if not rings:
                continue
            polys = []
            for ring in rings:
                if len(ring) >= 3:
                    polys.append(Polygon(ring))
            if not polys:
                continue
            geom = unary_union(polys)
            geojson_geom = {
                "type": geom.geom_type,
                "coordinates": list(geom.__geo_interface__["coordinates"])
            }
        except Exception:
            continue

        features.append({
            "type": "Feature",
            "geometry": geojson_geom,
            "properties": attrs,
        })

    return features


# ---------------------------------------------------------------------------
# Build GeoDataFrame
# ---------------------------------------------------------------------------

def fetch_parcels_as_gdf(
    state_cfg: dict,
    municipality_name: str,
    force_refresh: bool = False,
    **kwargs
) -> gpd.GeoDataFrame:
    """
    Fetch parcels and return as a GeoPandas GeoDataFrame in WGS84.
    """
    features = fetch_parcels_geojson(
        state_cfg, municipality_name, force_refresh=force_refresh, **kwargs
    )

    if not features:
        return gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")

    geometries = []
    rows = []
    for f in features:
        geom_dict = f.get("geometry")
        props = f.get("properties", {})

        if not geom_dict:
            continue

        try:
            geom = shape(geom_dict)
            if geom.is_empty or not geom.is_valid:
                geom = geom.buffer(0)
        except Exception:
            continue

        geometries.append(geom)
        rows.append(props)

    if not rows:
        return gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")

    gdf = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326")
    print(f"  Built GeoDataFrame: {len(gdf)} rows, {len(gdf.columns)} columns")
    print(f"  Columns: {[c for c in gdf.columns if c != 'geometry'][:15]}...")
    return gdf


# ---------------------------------------------------------------------------
# Spatial polygon query (stateless analysis pipeline)
# ---------------------------------------------------------------------------

def _try_get_then_post(query_url: str, params: dict, timeout: int = 60, max_retries: int = 8) -> dict:
    """
    Robust ArcGIS query: tries GET then POST, with exponential-backoff retries
    for transient connection errors (RST, ReadTimeout, ConnectError).

    Some ArcGIS hosts — notably MassGIS at services1.arcgis.com, fronted by
    Akamai — intermittently TLS-RST our connection in bursts. The window
    typically clears within 30–60s, so we keep trying with progressively
    longer waits up to ~60s total.

    Strategy:
      - Alternate POST/GET so a method-specific block doesn't trap us.
      - Browser-like User-Agent (some CDNs flag generic / bot UAs).
      - Backoff schedule: 1, 2, 4, 8, 15, 25, 40, 60 seconds (~155s max).
    """
    import httpx

    methods = ["POST", "GET", "POST", "GET", "POST", "GET", "POST", "GET", "POST"][:max_retries + 1]
    last_error: Exception | None = None
    last_arcgis_resp: dict | None = None

    # Browser-like UA — Akamai-fronted services1.arcgis.com appears more
    # tolerant of these than "PlinthSIP/1.0".
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    backoffs = [1, 2, 4, 8, 15, 25, 40, 60]

    for attempt, method in enumerate(methods):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, http2=False) as client:
                if method == "GET":
                    full_url = query_url + "?" + urlencode(params)
                    r = client.get(full_url, headers=headers)
                else:
                    post_headers = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
                    r = client.post(query_url, data=params, headers=post_headers)
                r.raise_for_status()
                resp = r.json()
                if "error" not in resp:
                    if attempt > 0:
                        print(f"  [spatial] {method} succeeded on attempt {attempt + 1}")
                    return resp
                last_arcgis_resp = resp
                print(f"  [spatial] {method} returned ArcGIS error on attempt {attempt + 1}, retrying...")
        except Exception as e:
            last_error = e
            print(f"  [spatial] {method} failed on attempt {attempt + 1} ({type(e).__name__}: {str(e)[:120]})")

        if attempt < len(methods) - 1:
            wait = backoffs[min(attempt, len(backoffs) - 1)]
            time.sleep(wait)

    if last_arcgis_resp is not None:
        return last_arcgis_resp
    raise last_error or RuntimeError(f"Spatial query failed after {len(methods)} attempts")


def fetch_parcels_in_polygon(state_code: str, polygon_geojson: dict, max_parcels: int = 5000) -> list[dict]:
    """
    Query ArcGIS REST with a spatial polygon filter.
    Returns raw GeoJSON features intersecting the polygon.

    Paginates automatically: ArcGIS servers cap at maxRecordCount (typically 1000).
    Fetches pages in parallel to avoid gaps in dense neighborhoods.
    Deduplicates by OBJECTID after merging all pages.
    """
    state_cfg = get_state_config(state_code)
    if not state_cfg:
        supported = [k for k, v in STATE_REGISTRY.items() if v.get("status") != "planned"]
        raise ValueError(f"State '{state_code}' not supported. Supported: {supported}")

    base_url = state_cfg["parcel_service_url"].rstrip("/")
    query_url = base_url + "/query"

    # Convert GeoJSON polygon to ArcGIS geometry format
    arcgis_geom = {
        "rings": polygon_geojson["coordinates"],
        "spatialReference": {"wkid": 4326},
    }

    base_params = {
        "geometry": json.dumps(arcgis_geom),
        "geometryType": "esriGeometryPolygon",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": "*",
        "returnGeometry": "true",
        "f": "geojson",
    }

    print(f"  [spatial] Querying {state_code} ArcGIS with polygon filter...")

    # Discover the server's actual max record count
    page_size = 1000
    try:
        info = _fetch_json(base_url + "?f=json", timeout=15)
        server_max = info.get("maxRecordCount", 1000)
        page_size = min(int(server_max), 1000)  # Cap at 1000 for reliability
        print(f"  [spatial] Server maxRecordCount={server_max}, using page_size={page_size}")
    except Exception:
        print(f"  [spatial] Could not query server info, using page_size={page_size}")

    # Fetch first page
    first_params = {**base_params, "resultRecordCount": str(page_size), "resultOffset": "0"}
    try:
        first_resp = _try_get_then_post(query_url, first_params)
    except Exception as e:
        raise ValueError(f"Spatial query failed for {state_code}: {type(e).__name__}: {e}")

    if "error" in first_resp:
        msg = first_resp["error"].get("message", str(first_resp["error"]))
        raise ValueError(f"ArcGIS error for {state_code}: {msg}")

    all_features = first_resp.get("features", [])
    first_count = len(all_features)
    print(f"  [spatial] Page 1: {first_count} features")

    if first_count < page_size:
        # Single page — done
        print(f"  [spatial] Got {first_count} features from {state_code} (1 page)")
        return all_features

    # Need pagination — fetch remaining pages in parallel batches
    max_pages = max_parcels // page_size  # 5000 / 1000 = 5 pages max

    def _fetch_page(offset: int) -> list[dict]:
        params = {**base_params, "resultRecordCount": str(page_size), "resultOffset": str(offset)}
        try:
            resp = _try_get_then_post(query_url, params)
            if "error" in resp:
                print(f"  [spatial] Page at offset {offset} returned error, skipping")
                return []
            return resp.get("features", [])
        except Exception as e:
            print(f"  [spatial] Page at offset {offset} failed: {type(e).__name__}")
            return []

    offset = page_size
    page_num = 1

    while offset < max_parcels:
        # Build a batch of offsets to fetch in parallel (up to 5 at a time)
        offsets = [offset + i * page_size for i in range(5) if offset + i * page_size < max_parcels]
        if not offsets:
            break

        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(_fetch_page, o): o for o in offsets}
            batch_results = {}
            for f in as_completed(futures):
                batch_results[futures[f]] = f.result()

        any_full_page = False
        for o in sorted(offsets):
            page_features = batch_results.get(o, [])
            page_num += 1
            print(f"  [spatial] Page {page_num}: {len(page_features)} features (offset {o})")
            all_features.extend(page_features)
            if len(page_features) >= page_size:
                any_full_page = True

        if not any_full_page:
            break  # All pages were partial — we've reached the end

        offset += len(offsets) * page_size

    # Deduplicate by OBJECTID
    seen = set()
    unique = []
    for feat in all_features:
        oid = (feat.get("properties") or {}).get("OBJECTID") or feat.get("id")
        if oid is not None:
            if oid in seen:
                continue
            seen.add(oid)
        unique.append(feat)

    print(f"  [spatial] Total: {len(unique)} unique features ({len(all_features)} raw, {page_num} pages) from {state_code}")
    return unique


def normalize_arcgis_feature(feature: dict, state_code: str, municipality_id: str) -> dict:
    """
    Normalize a raw ArcGIS GeoJSON feature into Plinth parcel schema.
    Uses the state's field_map from the registry.
    """
    state_cfg = get_state_config(state_code)
    if not state_cfg:
        return {}

    field_map = state_cfg.get("field_map", {})
    lot_unit = state_cfg.get("lot_size_unit", "sqft")
    props = feature.get("properties", {})

    def _get(internal_key):
        source_field = field_map.get(internal_key)
        if not source_field:
            return None
        val = props.get(source_field)
        if val is None or str(val).strip() in ("", "None", "NULL", "null", "nan", "NaN"):
            return None
        return val

    # Lot size conversion
    lot_raw = _get("lot_size")
    lot_sqft = None
    if lot_raw is not None:
        try:
            lot_val = float(str(lot_raw).replace(",", ""))
            lot_sqft = lot_val * 43560 if lot_unit == "acres" else lot_val
        except (ValueError, TypeError):
            pass

    # NY fallback: use SQ_FT field if CALC_ACRES gives 0 or None
    extra_fields = (state_cfg or {}).get("extra_fields", {})
    if (lot_sqft is None or lot_sqft <= 0) and extra_fields.get("lot_size_sqft"):
        sqft_raw = props.get(extra_fields["lot_size_sqft"])
        if sqft_raw is not None:
            try:
                sqft_val = float(str(sqft_raw).replace(",", ""))
                if sqft_val > 0:
                    lot_sqft = sqft_val
            except (ValueError, TypeError):
                pass

    # Building area
    bld_raw = _get("bld_area")
    bld_sqft = None
    if bld_raw is not None:
        try:
            bld_sqft = float(str(bld_raw).replace(",", ""))
        except (ValueError, TypeError):
            pass

    parcel_id_raw = _get("parcel_id")
    if not parcel_id_raw:
        parcel_id_raw = f"unknown_{id(feature)}"

    zoning = _get("zoning_code")
    if zoning:
        zoning = str(zoning).strip()

    use_code = _get("use_code")
    land_use_type = str(use_code).strip() if use_code else None

    # Extract extra fields (frontage, depth, county, zip, etc.)
    frontage_ft = None
    depth_ft = None
    county_name = None
    zip_code = None
    muni_name_raw = None
    year_built = None
    for internal_key, source_field in extra_fields.items():
        val = props.get(source_field)
        if val is None or str(val).strip() in ("", "None", "NULL", "0"):
            continue
        if internal_key == "frontage_ft":
            try: frontage_ft = float(val)
            except (ValueError, TypeError): pass
        elif internal_key == "depth_ft":
            try: depth_ft = float(val)
            except (ValueError, TypeError): pass
        elif internal_key == "county_name":
            county_name = str(val).strip()
        elif internal_key == "zip_code":
            zip_code = str(val).strip()
        elif internal_key == "muni_name":
            muni_name_raw = str(val).strip()
        elif internal_key == "year_built":
            try: year_built = int(val)
            except (ValueError, TypeError): pass

    # Convert GeoJSON geometry → Shapely so buildable_envelope_rule can run
    geojson_geom = feature.get("geometry")
    geometry_shapely = None
    if geojson_geom:
        try:
            from shapely.geometry import shape as shapely_shape
            geometry_shapely = shapely_shape(geojson_geom)
        except Exception:
            pass

    # calc_epsg from state config (needed for projection in buildable_envelope_rule)
    calc_epsg_str = (state_cfg or {}).get("calc_crs", "EPSG:4326")
    try:
        calc_epsg = int(calc_epsg_str.split(":")[1])
    except Exception:
        calc_epsg = 4326

    # Infer existing structure from building area or year_built
    existing_structure_count = None
    if bld_sqft and bld_sqft > 0:
        existing_structure_count = 1
    elif year_built and year_built > 0:
        existing_structure_count = 1

    return {
        "parcel_id": str(parcel_id_raw).strip(),
        "municipality_id": municipality_id,
        "address": _get("address"),
        "owner_name": _get("owner_name"),
        "zoning_code": zoning,
        "lot_area_sqft": lot_sqft,
        "land_use_type": land_use_type,
        "existing_structure_count": existing_structure_count,
        "existing_building_footprint_area": bld_sqft,
        "geometry_geojson": geojson_geom,
        "geometry_shapely": geometry_shapely,
        "calc_epsg": calc_epsg,
        # Extra fields for improved accuracy
        "frontage_ft": frontage_ft,
        "depth_ft": depth_ft,
        "county_name": county_name,
        "zip_code": zip_code,
        "muni_name": muni_name_raw,
        "year_built": year_built,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m app.agents.gis_fetcher <municipality_name> <state>")
        print("  e.g. python -m app.agents.gis_fetcher Acton MA")
        sys.exit(1)

    muni_name = sys.argv[1]
    state = sys.argv[2].upper()
    force = "--force-refresh" in sys.argv

    cfg = get_state_config(state)
    if not cfg:
        print(f"State '{state}' not in registry.")
        sys.exit(1)

    if cfg.get("parcel_source") not in ("arcgis_rest",):
        print(f"State '{state}' uses {cfg.get('parcel_source')} — not yet supported by gis_fetcher.")
        sys.exit(1)

    print(f"Plinth SIP — Generic GIS Fetcher")
    print(f"Municipality: {muni_name}, {state}")
    print(f"Service: {cfg.get('parcel_service_url', 'N/A')}")
    print()

    try:
        gdf = fetch_parcels_as_gdf(cfg, muni_name, force_refresh=force)
        print(f"\nSuccess: {len(gdf)} parcels fetched")
        print(f"Columns: {list(gdf.columns)}")
        if len(gdf) > 0:
            print("\nSample row:")
            row = gdf.iloc[0]
            for col in gdf.columns:
                if col != "geometry":
                    print(f"  {col}: {row[col]}")
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
