"""USFWS National Wetlands Inventory - on-demand WFS proxy.

Called per-parcel from `infrastructure.wetland_coverage_pct`. We don't
bulk-load the full NWI dataset (~30 GB nationally); instead, we hit
USFWS's public ArcGIS REST endpoint for the parcel's bounding envelope,
intersect with the parcel polygon in Shapely, and return coverage %.

Result is cached at the analyzer level via `parcel_datacenter_analyses`
(the wetland number is part of the cached report). NWI changes
infrequently; per-parcel staleness up to a refresh cycle is fine.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

log = logging.getLogger(__name__)

# Public USFWS NWI mapserver. Layer 0 = wetlands polygons.
DEFAULT_NWI_URL = "https://www.fws.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0/query"

NWI_TIMEOUT_S = 30.0
USER_AGENT = "PlinthSIP-nwi/0.1 (+https://plinth.example)"


def _nwi_url() -> str:
    return os.environ.get("PLINTH_NWI_QUERY_URL", DEFAULT_NWI_URL)


def fetch_wetlands_for_envelope(
    west: float, south: float, east: float, north: float,
    *, timeout: float = NWI_TIMEOUT_S, session: Optional[requests.Session] = None,
) -> list[dict]:
    """Fetch wetland polygon Features intersecting the WGS84 envelope.

    Returns an empty list on network failure, with a warning logged.
    """
    sess = session or requests.Session()
    params = {
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "ATTRIBUTE,WETLAND_TYPE,ACRES",
        "outSR": 4326,
        "f": "geojson",
        "where": "1=1",
        "returnGeometry": "true",
    }
    try:
        resp = sess.get(
            _nwi_url(),
            params=params,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.warning("USFWS NWI fetch failed: %s", e)
        return []
    return data.get("features", []) or []


def coverage_pct_from_features(parcel_geom_wkt: str, wetland_features: list[dict]) -> Optional[float]:
    """Compute (wetland intersect parcel area) / (parcel area) * 100.

    Pure helper: takes a parcel polygon WKT and a list of GeoJSON
    Features. Returns coverage % rounded to 1 decimal, or None if the
    parcel polygon is invalid / has zero area.
    """
    from shapely import wkt as shapely_wkt
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from shapely.validation import make_valid

    try:
        parcel = shapely_wkt.loads(parcel_geom_wkt)
    except Exception:
        return None
    if parcel.is_empty:
        return None
    if not parcel.is_valid:
        parcel = make_valid(parcel)
    parcel_area = parcel.area
    if parcel_area <= 0:
        return None

    if not wetland_features:
        return 0.0

    geoms = []
    for f in wetland_features:
        g = f.get("geometry")
        if not g:
            continue
        try:
            shp = shape(g)
            if not shp.is_valid:
                shp = make_valid(shp)
            geoms.append(shp)
        except Exception:
            continue

    if not geoms:
        return 0.0
    union = unary_union(geoms)
    inter = parcel.intersection(union)
    if inter.is_empty:
        return 0.0
    return round(100.0 * inter.area / parcel_area, 1)


def wetland_coverage_pct(
    parcel_geom_wkt: Optional[str],
    *,
    fetcher=fetch_wetlands_for_envelope,
) -> Optional[float]:
    """High-level entry point. Returns coverage % or None on failure.

    `fetcher` is injected so tests can supply offline data.
    """
    if not parcel_geom_wkt:
        return None
    from shapely import wkt as shapely_wkt
    try:
        geom = shapely_wkt.loads(parcel_geom_wkt)
    except Exception:
        return None
    if geom.is_empty:
        return None
    minx, miny, maxx, maxy = geom.bounds
    feats = fetcher(minx, miny, maxx, maxy)
    return coverage_pct_from_features(parcel_geom_wkt, feats)
