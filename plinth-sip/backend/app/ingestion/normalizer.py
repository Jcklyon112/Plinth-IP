"""
Normalization layer: converts raw GeoDataFrame rows into the universal Plinth parcel schema.
Each municipality has an adapter that maps source fields to internal fields.
"""
import geopandas as gpd
from shapely.geometry import mapping
import json


def normalize_parcel(raw_row: dict, geometry, adapter: dict, municipality_id: str) -> dict:
    """
    Apply an adapter mapping to a raw parcel row and return a normalized parcel dict.

    adapter is a dict with keys:
        field_map: { internal_field: source_field_name_or_None }
        use_code_map: { source_use_code: internal_land_use_type }
        defaults: { internal_field: default_value }
    """
    field_map = adapter.get("field_map", {})
    use_code_map = adapter.get("use_code_map", {})
    defaults = adapter.get("defaults", {})

    def get_field(internal_name: str):
        source_field = field_map.get(internal_name)
        if source_field and source_field in raw_row:
            val = raw_row[source_field]
            return val if val not in (None, "", "NULL", "null") else defaults.get(internal_name)
        return defaults.get(internal_name)

    parcel_id = get_field("parcel_id")
    if not parcel_id:
        return None

    raw_use = get_field("land_use_type")
    land_use_type = use_code_map.get(str(raw_use), raw_use) if raw_use else None

    lot_area = get_field("lot_area_sqft")
    try:
        lot_area = float(lot_area) if lot_area is not None else None
    except (ValueError, TypeError):
        lot_area = None

    footprint = get_field("existing_building_footprint_area")
    try:
        footprint = float(footprint) if footprint is not None else None
    except (ValueError, TypeError):
        footprint = None

    structure_count = get_field("existing_structure_count")
    try:
        structure_count = int(structure_count) if structure_count is not None else None
    except (ValueError, TypeError):
        structure_count = None

    # Geometry: convert to WGS84 GeoJSON string
    geom_geojson = None
    if geometry is not None:
        try:
            geom_geojson = json.dumps(mapping(geometry))
        except Exception:
            geom_geojson = None

    return {
        "parcel_id": str(parcel_id),
        "municipality_id": municipality_id,
        "address": get_field("address"),
        "owner_name": get_field("owner_name"),
        "owner_mailing_address": get_field("owner_mailing_address"),
        "zoning_code": get_field("zoning_code"),
        "lot_area_sqft": lot_area,
        "land_use_type": land_use_type,
        "assessed_use": get_field("assessed_use"),
        "existing_building_footprint_area": footprint,
        "existing_structure_count": structure_count,
        "geometry_geojson": geom_geojson,
        "raw_source_references": {k: str(v) for k, v in raw_row.items()},
    }
