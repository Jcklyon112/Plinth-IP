"""
Elevation / slope service.

Queries the MassGIS statewide LiDAR-derived 1 m DEM ImageServer for
slope statistics within a parcel polygon. Uses ArcGIS's server-side
`computeStatisticsHistograms` endpoint with the `Slope` raster function,
so we get min/max/mean/stddev back in a single small JSON response per
parcel — no raster pixels are transferred.

For other states, register additional ImageServer endpoints in
`ELEVATION_SERVICES` (the per-parcel function picks the right one by
state code).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlencode

import httpx
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry


# ---------------------------------------------------------------------------
# Per-state DEM ImageServer registry
# ---------------------------------------------------------------------------

ELEVATION_SERVICES: dict[str, dict] = {
    "MA": {
        # MassGIS LiDAR-derived 1 m statewide DEM (integer meters).
        # Service is publicly accessible, no auth required.
        "image_service": (
            "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services"
            "/LiDAR/ELEVATION_LIDAR_INT_2013to2021/ImageServer"
        ),
        "z_factor": 1.0,        # elevation and XY in meters → 1.0
        "pixel_size_m": 1.0,
        "label": "MassGIS LiDAR 1 m DEM (2013–2021)",
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def slope_stats_for_geometry(
    geom: BaseGeometry,
    state_code: str,
    timeout: int = 20,
) -> dict | None:
    """
    Return slope statistics in DEGREES for the given parcel geometry, or
    None if the service is unavailable, the polygon is invalid, or the
    state has no registered DEM service.

    Output shape:
      {
        "min": float,           # degrees
        "max": float,
        "mean": float,
        "stddev": float,
        "count": int,           # pixel count sampled
        "source": "MassGIS LiDAR 1 m DEM (2013–2021)",
        "pixel_size_m": 1.0,
      }
    """
    svc = ELEVATION_SERVICES.get((state_code or "").upper())
    if not svc:
        return None
    if geom is None or geom.is_empty:
        return None

    rings = _shapely_to_arcgis_rings(geom)
    if not rings:
        return None

    arcgis_geom = {"rings": rings, "spatialReference": {"wkid": 4326}}
    rendering_rule = {
        "rasterFunction": "Slope",
        "rasterFunctionArguments": {
            "ZFactor": svc.get("z_factor", 1.0),
            "SlopeType": 1,  # 1 = DEGREE, 2 = PERCENT_RISE
        },
    }

    params = {
        "geometry": json.dumps(arcgis_geom),
        "geometryType": "esriGeometryPolygon",
        "renderingRule": json.dumps(rendering_rule),
        "f": "json",
    }

    url = svc["image_service"].rstrip("/") + "/computeStatisticsHistograms"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(
                url + "?" + urlencode(params),
                headers={"User-Agent": "PlinthSIP/1.0 (elevation_service)"},
            )
            r.raise_for_status()
            data = r.json()
    except Exception:  # noqa: BLE001
        return None

    if "error" in data:
        return None

    stats_list = data.get("statistics") or []
    if not stats_list:
        return None
    s = stats_list[0]
    try:
        return {
            "min": float(s.get("min", 0.0)),
            "max": float(s.get("max", 0.0)),
            "mean": float(s.get("mean", 0.0)),
            "stddev": float(s.get("standardDeviation", 0.0)),
            "count": int(s.get("count", 0)),
            "source": svc.get("label", "DEM"),
            "pixel_size_m": svc.get("pixel_size_m"),
        }
    except (TypeError, ValueError):
        return None


def annotate_parcels_with_slope(
    parcels: list[dict],
    state_code: str,
    max_workers: int = 6,
) -> int:
    """
    Mutate each parcel dict in place: set parcel["slope_stats"] = dict|None.

    Returns the number of parcels for which slope was successfully computed.
    Skips parcels lacking a usable shapely geometry. Runs requests in
    parallel; the DEM service tolerates moderate concurrency (~6 workers).
    """
    if not parcels:
        return 0
    if (state_code or "").upper() not in ELEVATION_SERVICES:
        return 0

    def _one(p: dict) -> tuple[dict, dict | None]:
        geom = p.get("geometry_shapely")
        if geom is None:
            geojson = p.get("geometry") or p.get("geometry_geojson")
            if geojson:
                try:
                    from shapely.geometry import shape as shapely_shape
                    geom = shapely_shape(geojson)
                except Exception:
                    geom = None
        if geom is None or geom.is_empty:
            return p, None
        return p, slope_stats_for_geometry(geom, state_code)

    successes = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_one, p) for p in parcels]
        for fut in as_completed(futures):
            p, stats = fut.result()
            p["slope_stats"] = stats
            if stats is not None:
                successes += 1
    return successes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shapely_to_arcgis_rings(geom: BaseGeometry) -> list[list[list[float]]]:
    """
    Convert a Shapely Polygon/MultiPolygon to ArcGIS REST `rings` format
    (list of ring coordinate arrays). For MultiPolygons all exterior rings
    plus their interiors are flattened — the server treats this as a single
    multi-ring geometry.
    """
    gj = mapping(geom)
    gtype = gj.get("type")
    coords = gj.get("coordinates", [])

    rings: list[list[list[float]]] = []
    if gtype == "Polygon":
        for ring in coords:
            rings.append([[float(x), float(y)] for x, y, *_ in ring])
    elif gtype == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                rings.append([[float(x), float(y)] for x, y, *_ in ring])
    return rings
