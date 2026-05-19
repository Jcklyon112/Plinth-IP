"""
Overlay Layer Registry — declarative list of GIS feature layers used for
live spatial intersection against parcels.

Each layer is a publicly accessible ArcGIS REST FeatureServer / MapServer
endpoint. Endpoints verified to return service-info JSON with no auth.

Schema:
  service_url      — full /MapServer/<n> or /FeatureServer/<n> URL, or None if unverified
  out_fields       — attribute fields to request (kept small to reduce payload)
  constraint_level — "hard_block" | "review" | "soft_constraint"
                     hard_block       → overlay rule returns FAIL
                     review           → overlay rule returns CONDITIONAL
                     soft_constraint  → noted but does not change result by itself
  buffer_ft        — buffer applied AROUND the overlay geometry before parcel
                     intersection (e.g. 100 ft for MA wetlands buffer zone)
  label            — human-readable name shown in explanations
  in_sr            — native spatial reference of the layer (we always send
                     query geometry as 4326 and let server reproject)
  states           — list of state codes the layer applies to; ["*"] = all states
  verified         — endpoint test query at registration time confirmed it
                     returns service info (not whether it currently has data)

Notes:
  Spatial reference: all queries go in/out as EPSG:4326. Buffering is done
  client-side in EPSG:26986 (NAD83 MA State Plane meters) to get accurate
  foot-based distances; this is fine for Massachusetts but a different
  meters-CRS should be used for other states.
"""

OVERLAY_REGISTRY: dict[str, dict] = {
    # ─── Federal (nationwide) ─────────────────────────────────────────────
    "fema_flood_zone": {
        "service_url": "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28",
        "out_fields": ["FLD_ZONE", "ZONE_SUBTY", "SFHA_TF", "STATIC_BFE"],
        "constraint_level": "hard_block",
        "buffer_ft": 0,
        "label": "FEMA Special Flood Hazard Area",
        "in_sr": 4269,
        "states": ["*"],
        "verified": True,
    },

    # ─── MassGIS / MassDEP / NHESP (Massachusetts) ────────────────────────
    "massdep_wetlands": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/DEP_Wetlands/MapServer/1",
        "out_fields": ["WETCODE", "IT_VALDESC", "POLY_CODE", "AREAACRES"],
        "constraint_level": "hard_block",
        "buffer_ft": 100,
        "label": "MA Wetlands (100-ft WPA buffer)",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "massdep_riverfront": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/Hydro_25k/MapServer/1",
        "out_fields": ["NAME", "ARC_CODE", "SARISNAME", "SARISCODE"],
        "constraint_level": "review",
        "buffer_ft": 200,
        "label": "MA Riverfront Area (200 ft from perennial stream)",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "nhesp_priority_habitat": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/NHESP_Priority_Habitats/MapServer/0",
        "out_fields": ["PRIHAB_ID", "VERSION"],
        "constraint_level": "hard_block",
        "buffer_ft": 0,
        "label": "NHESP Priority Habitat (MESA review)",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "nhesp_estimated_habitat": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/NHESP_Estimated_Habitats/MapServer/0",
        "out_fields": ["ESTHAB_ID", "VERSION"],
        "constraint_level": "review",
        "buffer_ft": 0,
        "label": "NHESP Estimated Habitat of Rare Wildlife",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "nhesp_certified_vernal_pool": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/NHESP_Certified_Vernal_Pools/MapServer/0",
        "out_fields": ["CVP_NUM", "CRITERIA", "CERTIFIED"],
        "constraint_level": "hard_block",
        "buffer_ft": 100,
        "label": "Certified Vernal Pool (100-ft WPA buffer)",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "nhesp_potential_vernal_pool": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/NHESP_Potential_Vernal_Pools/MapServer/0",
        "out_fields": ["PVP_NUMBER", "TOWN"],
        "constraint_level": "review",
        "buffer_ft": 0,
        "label": "NHESP Potential Vernal Pool",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "acec": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/ACECs/MapServer/0",
        "out_fields": ["ACECID", "NAME", "DES_DATE"],
        "constraint_level": "review",
        "buffer_ft": 0,
        "label": "Area of Critical Environmental Concern",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "massdep_21e_sites": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/C21e/MapServer/0",
        "out_fields": ["RTN", "NAME", "ADDRESS", "TOWN", "STATUS"],
        "constraint_level": "review",
        "buffer_ft": 0,
        "label": "MassDEP Ch. 21E Tier-Classified Site",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "massdep_aul_sites": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/AUL_Sites/MapServer/0",
        "out_fields": ["RTN", "NAME", "ADDRESS", "TOWN", "STATUS", "RAO_CLASS"],
        "constraint_level": "hard_block",
        "buffer_ft": 0,
        "label": "MassDEP Activity & Use Limitation (AUL) Site",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "wellhead_zone_ii": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/IWPA_Zone2/MapServer/0",
        "out_fields": ["ZII_NUM", "PWS_ID", "SUPPLIER", "TOWN"],
        "constraint_level": "review",
        "buffer_ft": 0,
        "label": "Wellhead Protection Area (Zone II)",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "wellhead_iwpa": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/IWPA_Zone2/MapServer/1",
        "out_fields": ["SOURCE_ID", "SITE_NAME", "SUPPLIER", "TOWN", "IWPA_FT"],
        "constraint_level": "review",
        "buffer_ft": 0,
        "label": "Interim Wellhead Protection Area (IWPA)",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "protected_open_space": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/openspace/MapServer/0",
        "out_fields": ["SITE_NAME", "FEE_OWNER", "OWNER_TYPE", "PRIM_PURP", "LEV_PROT", "OS_TYPE", "CR_REF"],
        "constraint_level": "hard_block",
        "buffer_ft": 0,
        "label": "Protected Open Space / Conservation Restriction",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
    "mhc_historic_areas": {
        "service_url": "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/MHC_Inventory/MapServer/2",
        "out_fields": ["MHCN", "TYPE", "DESIGNATIO", "HISTORIC_N", "TOWN_NAME"],
        "constraint_level": "review",
        "buffer_ft": 0,
        "label": "MHC Historic Inventory Area",
        "in_sr": 26986,
        "states": ["MA"],
        "verified": True,
    },
}


# Per-state CRS to use for client-side buffering (must be a meters-based CRS).
# Used by the overlay service when buffering layer geometries.
STATE_BUFFER_CRS = {
    "MA": "EPSG:26986",  # NAD83 / Massachusetts Mainland (meters)
    "*": "EPSG:3857",    # Web Mercator fallback — distortion grows with latitude
}


def overlays_for_state(state_code: str) -> dict[str, dict]:
    """Return the subset of overlays that apply to a given state."""
    state_upper = (state_code or "").upper()
    return {
        layer_id: cfg
        for layer_id, cfg in OVERLAY_REGISTRY.items()
        if cfg.get("verified") and (
            "*" in cfg.get("states", [])
            or state_upper in cfg.get("states", [])
        )
    }


def buffer_crs_for_state(state_code: str) -> str:
    return STATE_BUFFER_CRS.get((state_code or "").upper(), STATE_BUFFER_CRS["*"])
