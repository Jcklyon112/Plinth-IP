"""
Overlay Service — live spatial intersection of parcels against environmental,
regulatory, and protected-area layers.

Strategy:
  1. User draws a polygon on the map.
  2. For each registered overlay layer that applies to the state,
     query the ArcGIS REST endpoint ONCE with the user's polygon as a
     spatial filter. Cache the returned features in memory.
  3. Apply per-layer buffer (e.g. 100 ft for wetlands) to the cached
     overlay geometries — buffer is done in a meters-based CRS for accuracy.
  4. For each parcel, run shapely intersects() against each cached layer.
  5. Emit a list of constraint flags (strings) plus structured metadata
     (which layer, attributes of the intersected feature, distance) so
     downstream rules and explanations can use them.

Cost: O(L) ArcGIS calls per shape-draw (L = # of registered layers, ~14 for MA),
not O(L × N) where N is the parcel count. Per-parcel work is local Shapely
geometry math which is fast.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlencode

import httpx
import geopandas as gpd
from shapely.geometry import shape as shapely_shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.agents.overlay_registry import (
    OVERLAY_REGISTRY,
    buffer_crs_for_state,
    overlays_for_state,
)


# ---------------------------------------------------------------------------
# ArcGIS query helpers
# ---------------------------------------------------------------------------

_USER_AGENT = "PlinthSIP/1.0 (overlay_service)"


def _arcgis_query_polygon(
    service_url: str,
    polygon_geojson: dict,
    out_fields: list[str],
    timeout: int = 60,
) -> list[dict]:
    """
    Run a polygon-intersect query against an ArcGIS REST layer.
    Returns the raw `features` list from the GeoJSON response.

    The polygon is sent as `esriGeometryPolygon` in WGS84 (inSR=4326);
    the server reprojects to its native SRS.
    """
    arcgis_geom = {
        "rings": polygon_geojson["coordinates"],
        "spatialReference": {"wkid": 4326},
    }
    params = {
        "geometry": json.dumps(arcgis_geom),
        "geometryType": "esriGeometryPolygon",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": ",".join(out_fields) if out_fields else "*",
        "returnGeometry": "true",
        "f": "geojson",
        "resultRecordCount": "2000",
    }

    query_url = service_url.rstrip("/") + "/query"

    last_err: Exception | None = None
    for method in ("GET", "POST"):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                if method == "GET":
                    full = query_url + "?" + urlencode(params)
                    r = client.get(full, headers={"User-Agent": _USER_AGENT})
                else:
                    r = client.post(query_url, data=params, headers={"User-Agent": _USER_AGENT})
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    last_err = RuntimeError(f"ArcGIS error: {data['error']}")
                    continue
                if data.get("type") == "FeatureCollection":
                    return data.get("features", [])
                # Fallback: classic ArcGIS JSON
                return _arcgis_to_geojson(data)
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue

    if last_err:
        raise last_err
    return []


def _arcgis_to_geojson(data: dict) -> list[dict]:
    """Convert classic ArcGIS JSON (rings) to GeoJSON-shaped features."""
    out = []
    for f in data.get("features", []):
        geom = f.get("geometry", {})
        rings = geom.get("rings")
        if not rings:
            continue
        gj = {"type": "Polygon" if len(rings) == 1 else "MultiPolygon"}
        gj["coordinates"] = rings if len(rings) == 1 else [rings]
        out.append({"type": "Feature", "geometry": gj, "properties": f.get("attributes", {})})
    return out


# ---------------------------------------------------------------------------
# Layer fetch + buffer
# ---------------------------------------------------------------------------

def _features_to_gdf(features: list[dict], crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    """Convert a list of GeoJSON features into a GeoDataFrame, dropping invalid geoms."""
    rows: list[dict] = []
    geoms: list[BaseGeometry] = []
    for f in features:
        geom_dict = f.get("geometry")
        if not geom_dict:
            continue
        try:
            g = shapely_shape(geom_dict)
            if g.is_empty:
                continue
            if not g.is_valid:
                g = g.buffer(0)
            if g.is_empty:
                continue
        except Exception:
            continue
        geoms.append(g)
        rows.append(f.get("properties", {}) or {})
    if not rows:
        return gpd.GeoDataFrame(columns=["geometry"], crs=crs)
    return gpd.GeoDataFrame(rows, geometry=geoms, crs=crs)


def _buffer_gdf(gdf: gpd.GeoDataFrame, buffer_ft: float, meters_crs: str) -> gpd.GeoDataFrame:
    """Buffer polygons by buffer_ft. Reprojects to meters_crs, buffers, returns to WGS84."""
    if buffer_ft <= 0 or gdf.empty:
        return gdf
    buffer_m = buffer_ft * 0.3048
    proj = gdf.to_crs(meters_crs)
    proj["geometry"] = proj.geometry.buffer(buffer_m)
    return proj.to_crs("EPSG:4326")


def _fetch_layer(
    layer_id: str,
    layer_cfg: dict,
    polygon_geojson: dict,
    meters_crs: str,
) -> tuple[str, gpd.GeoDataFrame]:
    """Fetch one overlay layer and return (layer_id, buffered GeoDataFrame)."""
    service_url = layer_cfg.get("service_url")
    if not service_url:
        return layer_id, gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")

    out_fields = layer_cfg.get("out_fields", []) or []
    buffer_ft = float(layer_cfg.get("buffer_ft") or 0)

    # When the layer has a buffer (e.g. wetlands 100 ft), the user polygon
    # itself should be expanded by buffer_ft so we capture overlay features
    # that lie outside the polygon but whose buffer still intersects it.
    fetch_polygon_geojson = polygon_geojson
    if buffer_ft > 0:
        try:
            user_gdf = gpd.GeoDataFrame(
                geometry=[shapely_shape(polygon_geojson)],
                crs="EPSG:4326",
            )
            buffered_user = _buffer_gdf(user_gdf, buffer_ft, meters_crs)
            buffered_geom = buffered_user.geometry.iloc[0]
            fetch_polygon_geojson = json.loads(gpd.GeoSeries([buffered_geom], crs="EPSG:4326").to_json())["features"][0]["geometry"]
        except Exception:
            fetch_polygon_geojson = polygon_geojson

    t0 = time.time()
    try:
        features = _arcgis_query_polygon(service_url, fetch_polygon_geojson, out_fields)
    except Exception as e:  # noqa: BLE001
        print(f"  [overlay] {layer_id}: fetch failed ({type(e).__name__}: {e})")
        return layer_id, gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")

    gdf = _features_to_gdf(features)
    if buffer_ft > 0 and not gdf.empty:
        gdf = _buffer_gdf(gdf, buffer_ft, meters_crs)

    elapsed = time.time() - t0
    print(f"  [overlay] {layer_id}: {len(gdf)} features ({elapsed:.1f}s)")
    return layer_id, gdf


def fetch_overlays_for_polygon(
    polygon_geojson: dict,
    state_code: str,
    max_workers: int = 8,
) -> dict[str, gpd.GeoDataFrame]:
    """
    For the given user-drawn polygon and state, fetch each applicable
    overlay layer in parallel. Returns dict layer_id → GeoDataFrame
    (geometries already buffered where applicable).
    """
    layers = overlays_for_state(state_code)
    if not layers:
        return {}

    meters_crs = buffer_crs_for_state(state_code)
    results: dict[str, gpd.GeoDataFrame] = {}

    print(f"  [overlay] fetching {len(layers)} layers for state={state_code}...")
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_fetch_layer, lid, cfg, polygon_geojson, meters_crs): lid
            for lid, cfg in layers.items()
        }
        for fut in as_completed(futures):
            lid = futures[fut]
            try:
                lid_out, gdf = fut.result()
                results[lid_out] = gdf
            except Exception as e:  # noqa: BLE001
                print(f"  [overlay] {lid}: worker error ({type(e).__name__}: {e})")
                results[lid] = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
    return results


# ---------------------------------------------------------------------------
# Per-parcel intersection
# ---------------------------------------------------------------------------

def intersect_parcel(
    parcel_geom: BaseGeometry | None,
    overlays: dict[str, gpd.GeoDataFrame],
) -> tuple[list[str], list[dict]]:
    """
    For a single parcel geometry (WGS84 Shapely), return:
      - constraints_flags: list of overlay_type strings (suitable for the
        existing overlay_constraints rule)
      - overlay_hits: list of dicts with structured detail per intersection:
          {
            "layer_id":      "massdep_wetlands",
            "label":         "MA Wetlands (100-ft WPA buffer)",
            "constraint_level": "hard_block",
            "buffer_ft":     100,
            "attributes":    {"WETCODE": "BVW", ...}
          }
        Only the FIRST intersecting feature per layer is reported (parcels
        rarely have multiple meaningful hits per layer; first is enough for
        the explanation, can be extended later).
    """
    if parcel_geom is None or parcel_geom.is_empty:
        return [], []

    flags: list[str] = []
    hits: list[dict] = []

    for layer_id, gdf in overlays.items():
        if gdf is None or gdf.empty:
            continue
        layer_cfg = OVERLAY_REGISTRY.get(layer_id, {})
        try:
            # spatial index speeds this up substantially when many features
            sindex = gdf.sindex
            candidate_idx = list(sindex.intersection(parcel_geom.bounds))
            if not candidate_idx:
                continue
            candidates = gdf.iloc[candidate_idx]
            mask = candidates.geometry.intersects(parcel_geom)
            matched = candidates[mask]
        except Exception:
            matched = gdf[gdf.geometry.intersects(parcel_geom)]

        if matched.empty:
            continue

        first = matched.iloc[0]
        attrs = {
            k: (None if str(v) == "nan" else v)
            for k, v in first.drop(labels=["geometry"]).to_dict().items()
        }
        flags.append(layer_id)
        hits.append({
            "layer_id": layer_id,
            "label": layer_cfg.get("label", layer_id),
            "constraint_level": layer_cfg.get("constraint_level", "review"),
            "buffer_ft": layer_cfg.get("buffer_ft", 0),
            "attributes": attrs,
        })

    return flags, hits


def annotate_parcels_with_overlays(
    parcels: list[dict],
    overlays: dict[str, gpd.GeoDataFrame],
) -> None:
    """
    Mutate each parcel dict in place, populating:
      parcel["constraints_flags"] — list[str], extends any pre-existing flags
      parcel["overlay_hits"]      — list[dict] of per-overlay detail
    """
    for p in parcels:
        geom = p.get("geometry_shapely")
        if geom is None:
            geojson = p.get("geometry") or p.get("geometry_geojson")
            if geojson:
                try:
                    geom = shapely_shape(geojson)
                except Exception:
                    geom = None
        flags, hits = intersect_parcel(geom, overlays)
        existing = list(p.get("constraints_flags") or [])
        # Preserve any pre-tagged flags but de-duplicate
        merged = existing + [f for f in flags if f not in existing]
        p["constraints_flags"] = merged
        p["overlay_hits"] = hits
