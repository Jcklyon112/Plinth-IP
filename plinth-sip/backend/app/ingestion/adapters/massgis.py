"""
MassGIS Standardized Parcel Adapter
Covers Massachusetts municipalities using the MassGIS L3 parcel standard.

Field reference: https://www.mass.gov/info-details/massgis-data-property-tax-parcels

NOTE: MassGIS changed field names between the old Assess shapefile and the
newer ArcGIS Feature Service API. This adapter handles both naming conventions:

  Old Assess shapefile  →  New Feature Service API
  ZONING_CD            →  ZONING
  BLDG_AREA            →  BLD_AREA
  NUM_BLDGS            →  UNITS
  MAIL_ADDR            →  OWN_ADDR
"""

MASSGIS_ADAPTER = {
    "field_map": {
        # Primary field names (ArcGIS Feature Service / modern format)
        "parcel_id":                        "LOC_ID",
        "address":                          "SITE_ADDR",
        "owner_name":                       "OWNER1",
        "owner_mailing_address":            "OWN_ADDR",
        "zoning_code":                      "ZONING",
        "lot_area_sqft":                    "LOT_SIZE",
        "land_use_type":                    "USE_CODE",
        "assessed_use":                     "USE_CODE",
        "existing_building_footprint_area": "BLD_AREA",
        "existing_structure_count":         "UNITS",
    },
    "field_map_fallbacks": {
        # If primary field is absent or null, try these in order (old shapefile names)
        "owner_mailing_address":            ["MAIL_ADDR"],
        "zoning_code":                      ["ZONING_CD"],
        "existing_building_footprint_area": ["BLDG_AREA"],
        "existing_structure_count":         ["NUM_BLDGS"],
    },
    "use_code_map": {
        "101": "single_family",
        "102": "condominium",
        "103": "mobile_home",
        "104": "two_family",
        "105": "three_family",
        "109": "single_family",
        "111": "apartment_4_8",
        "112": "apartment_9_plus",
        "113": "apartment_9_plus",
        "121": "farmland",
        "122": "farmland",
        "130": "commercial",
        "131": "commercial",
        "132": "commercial",
        "300": "industrial",
        "301": "industrial",
        "302": "industrial",
        "400": "mixed_use",
        "401": "mixed_use",
        "500": "recreational",
        "501": "recreational",
        "502": "recreational",
        "600": "exempt",
        "601": "exempt",
        "700": "vacant",
        "701": "vacant",
        "710": "vacant",
        "720": "vacant",
        "730": "vacant",
        "740": "vacant",
        "742": "vacant",
        "900": "undevelopable",
    },
    "defaults": {
        "land_use_type": "unknown",
        "existing_structure_count": None,
    },
    # MassGIS stores lot size in acres by default; multiply by 43560 to get sqft
    # Exception: if LOT_UNITS == "SF" the value is already in sqft
    "lot_area_sqft_multiplier": 43560.0,
}


def massgis_field_transform(raw_row: dict) -> dict:
    """
    Pre-process a MassGIS row before normalization:
    - Converts LOT_SIZE from acres to sqft (unless LOT_UNITS is SF)
    - Fills primary field names from fallback names when primary is absent/null
    """
    row = dict(raw_row)

    # Convert LOT_SIZE acres → sqft
    if "LOT_SIZE" in row and row["LOT_SIZE"] not in (None, "", "NULL", "None"):
        try:
            val = float(row["LOT_SIZE"])
            lot_units = str(row.get("LOT_UNITS", "")).strip().upper()
            if lot_units == "SF":
                row["LOT_SIZE"] = val          # already sqft
            else:
                row["LOT_SIZE"] = val * 43560.0  # acres → sqft
        except (ValueError, TypeError):
            row["LOT_SIZE"] = None

    # Normalize USE_CODE: MassGIS API returns 4-digit codes (e.g. 1010, 1040)
    # but the standard L3 spec uses 3-digit codes (101, 104).
    # Strip the trailing subcode digit when 4-digit format is detected.
    if "USE_CODE" in row and not _is_empty(row.get("USE_CODE")):
        try:
            code_str = str(int(float(row["USE_CODE"])))
            if len(code_str) == 4:
                row["USE_CODE"] = code_str[:3]
        except (ValueError, TypeError):
            pass

    # Fill primary field names from fallbacks when primary is missing or null
    fallbacks = MASSGIS_ADAPTER.get("field_map_fallbacks", {})
    primary_map = MASSGIS_ADAPTER["field_map"]

    def _is_empty(v):
        if v is None:
            return True
        if isinstance(v, float):
            import math
            return math.isnan(v)
        return str(v).strip() in ("", "NULL", "None", "nan")

    for internal, primary_field in primary_map.items():
        if _is_empty(row.get(primary_field)):
            for fallback in fallbacks.get(internal, []):
                fb_val = row.get(fallback)
                if not _is_empty(fb_val):
                    row[primary_field] = fb_val
                    break

    return row
