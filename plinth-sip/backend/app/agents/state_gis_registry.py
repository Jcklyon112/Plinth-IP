"""
State GIS Registry
Maps US states to their parcel data sources and field adapters.

Most Northeast states run ArcGIS REST APIs — the same pattern as MassGIS.
Once a state entry exists here, every municipality in that state works automatically.

Adding a new state = add one entry to STATE_REGISTRY.
"""

STATE_REGISTRY = {

    # ── Massachusetts ──────────────────────────────────────────────────────
    "MA": {
        "name": "Massachusetts",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services"
            "/Massachusetts_Property_Tax_Parcels/FeatureServer/0"
        ),
        "town_id_field": "TOWN_ID",          # field to filter by municipality
        "town_id_type": "numeric",           # lookup via TOWN_ID number
        "town_lookup_url": (                 # API to find TOWN_ID by name
            "https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services"
            "/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query"
        ),
        "poly_type_field": "POLY_TYPE",
        "poly_type_fee_value": "FEE",
        "calc_crs": "EPSG:26986",
        "field_map": {
            "parcel_id":    "LOC_ID",
            "address":      "SITE_ADDR",
            "owner_name":   "OWNER1",
            "owner_mail":   "OWN_ADDR",
            "zoning_code":  "ZONING",
            "lot_size":     "LOT_SIZE",      # acres
            "lot_units":    "LOT_UNITS",
            "use_code":     "USE_CODE",
            "bld_area":     "BLD_AREA",
            "units":        "UNITS",
        },
        # Optional fields — surface them if the assessor populated them.
        # year_built drives the electrical_service_rule heuristic.
        "extra_fields": {
            "year_built": "YEAR_BUILT",
            "muni_name":  "CITY",
            "zip_code":   "ZIP",
        },
        "lot_size_unit": "acres",
        "use_code_digits": 4,               # MA API returns 4-digit codes
        "status": "production",
    },

    # ── New Hampshire ──────────────────────────────────────────────────────
    "NH": {
        "name": "New Hampshire",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://nhgeodata.unh.edu/nhgeodata/rest/services"
            "/CAD/ParcelMosaic/MapServer/1"
        ),
        "town_id_field": "Town",
        "town_id_type": "string",
        "calc_crs": "EPSG:3437",
        "field_map": {
            "parcel_id":    "NH_GIS_ID",
            "address":      "StreetAddress",
            "owner_name":   "Name",
            "zoning_code":  "SLU",
            "lot_size":     "Shape_Area",    # sq ft from state plane
            "use_code":     "SLU",
            "bld_area":     "NBC",
        },
        "lot_size_unit": "sqft",
        "status": "beta",
    },

    # ── Vermont ────────────────────────────────────────────────────────────
    "VT": {
        "name": "Vermont",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://services1.arcgis.com/BkFxaEFNwHqX3tAw/arcgis/rest/services"
            "/FS_VCGI_OPENDATA_Cadastral_VTPARCELS_poly_standardized_parcels_SP_v1"
            "/FeatureServer/0"
        ),
        "town_id_field": "TNAME",
        "town_id_type": "string",
        "calc_crs": "EPSG:32145",
        "field_map": {
            "parcel_id":    "SPAN",
            "address":      "E911ADDR",
            "owner_name":   "OWNER1",
            "zoning_code":  "CAT",
            "lot_size":     "ACRESGL",
            "use_code":     "CAT",
            # Note: IMPRV_LV is improvement value in dollars, not sqft — no bld_area available
        },
        "lot_size_unit": "acres",
        "status": "beta",
    },

    # ── Connecticut ────────────────────────────────────────────────────────
    "CT": {
        "name": "Connecticut",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://services3.arcgis.com/3FL1kr7L4LvwA2Kb/arcgis/rest/services"
            "/Connecticut_CAMA_and_Parcel_Layer/FeatureServer/0"
        ),
        "town_id_field": "Town_Name",
        "town_id_type": "string",
        "calc_crs": "EPSG:26918",
        "field_map": {
            "parcel_id":    "Parcel_ID",
            "address":      "Location",
            "owner_name":   "Owner",
            "zoning_code":  "Zone",
            "lot_size":     "Land_Acres",
            "use_code":     "State_Use",
            "bld_area":     "Living_Area",
        },
        "lot_size_unit": "acres",
        "status": "beta",
    },

    # ── Maine ──────────────────────────────────────────────────────────────
    "ME": {
        "name": "Maine",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://gis.maine.gov/arcgis/rest/services"
            "/parcels/Maine_Parcels/MapServer/0"
        ),
        "town_id_field": "TOWN",
        "town_id_type": "string",
        "calc_crs": "EPSG:26919",
        "field_map": {
            "parcel_id":    "MAP_LOT",
            "address":      "LOCATION",
            "owner_name":   "OWNER",
            "zoning_code":  "ZONING",
            "lot_size":     "ACREAGE",
            "use_code":     "USE_CODE",
            "bld_area":     "BLDG_AREA",
        },
        "lot_size_unit": "acres",
        "status": "beta",
    },

    # ── Rhode Island ───────────────────────────────────────────────────────
    "RI": {
        "name": "Rhode Island",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://www.rigis.org/arcgis/rest/services"
            "/property/parcels/MapServer/0"
        ),
        "town_id_field": "CITY",
        "town_id_type": "string",
        "calc_crs": "EPSG:32618",
        "field_map": {
            "parcel_id":    "PLAT_LOT",
            "address":      "ADDRESS",
            "owner_name":   "OWNER",
            "zoning_code":  "ZONE",
            "lot_size":     "AREA_ACRES",
            "use_code":     "USE_CODE",
            "bld_area":     "BLDG_AREA",
        },
        "lot_size_unit": "acres",
        "status": "beta",
    },

    # ── New York ───────────────────────────────────────────────────────────
    "NY": {
        "name": "New York",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://gisservices.its.ny.gov/arcgis/rest/services"
            "/NYS_Tax_Parcels_Public/FeatureServer/1"
        ),
        "town_id_field": "MUNI_NAME",
        "town_id_type": "string",
        "calc_crs": "EPSG:32618",
        "field_map": {
            "parcel_id":    "PRINT_KEY",
            "address":      "PARCEL_ADDR",
            "owner_name":   "PRIMARY_OWNER",
            "zoning_code":  "PROP_CLASS",
            "lot_size":     "CALC_ACRES",       # CALC_ACRES is more reliable than ACRES
            "use_code":     "PROP_CLASS",
            "bld_area":     "SQFT_LIVING",
        },
        # Extra fields extracted by normalize_arcgis_feature for NY
        "extra_fields": {
            "lot_size_sqft":  "SQ_FT",          # lot size in sqft (NYC parcels have this)
            "frontage_ft":    "FRONT",           # frontage in feet
            "depth_ft":       "DEPTH",           # depth in feet
            "county_name":    "COUNTY_NAME",     # county for sewer/region detection
            "zip_code":       "LOC_ZIP",         # zip code for regional classification
            "muni_name":      "MUNI_NAME",       # municipality name
            "year_built":     "YR_BLT",          # year built
            "bldg_style":     "BLDG_STYLE_DESC", # building style description
            "total_av":       "TOTAL_AV",        # total assessed value
        },
        "lot_size_unit": "acres",
        "status": "beta",
    },

    # ── New Jersey ─────────────────────────────────────────────────────────
    "NJ": {
        "name": "New Jersey",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services"
            "/Parcels_Composite_NJ_WM/FeatureServer/0"
        ),
        "town_id_field": "MUN_NAME",
        "town_id_type": "string",
        "calc_crs": "EPSG:32618",
        "field_map": {
            "parcel_id":    "PAMS_PIN",
            "address":      "PROP_LOC",
            "owner_name":   "OWNER_NAME",
            "zoning_code":  "PROP_CLASS",
            "lot_size":     "CALC_ACRE",
            "use_code":     "PROP_CLASS",
            "bld_area":     "BLDG_DESC",
        },
        "lot_size_unit": "acres",
        "status": "beta",
    },

    # ── Florida (statewide) ────────────────────────────────────────────────
    "FL": {
        "name": "Florida",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://ca.dep.state.fl.us/arcgis/rest/services"
            "/OpenData/FDOR_PARCEL/MapServer/0"
        ),
        "town_id_field": "COUNTY",
        "town_id_type": "string",
        "calc_crs": "EPSG:3086",
        "field_map": {
            "parcel_id":    "PARCELNO",
            "address":      "APTS_ADDR",        # physical address field in FDOR
            "owner_name":   "OWN_NAME",
            "zoning_code":  "DOR_UC",           # DOR use code (01=SFR, 02=Mobile Home, etc.)
            "lot_size":     "LND_SQFOOT",       # land area in square feet (FDOR standard field)
            "use_code":     "DOR_UC",
            "bld_area":     "TOT_LVG_AREA",     # total living area sqft
        },
        "lot_size_unit": "sqft",
        "coverage": ["statewide"],
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Colorado — Denver County ───────────────────────────────────────────
    "CO_DENVER": {
        "name": "Colorado (Denver)",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services"
            "/PARCELS/FeatureServer/0"
        ),
        "town_id_field": None,
        "town_id_type": "none",
        "calc_crs": "EPSG:2876",
        "field_map": {
            "parcel_id":    "SCHEDNUM",
            "address":      "SITEADDR",
            "owner_name":   "OWNER_NAME",
            "zoning_code":  "ZONE_DIST",
            "lot_size":     "LAND_SQFT",
            "use_code":     "USE_CODE",
            "bld_area":     "BLDG_SQFT",
        },
        "lot_size_unit": "sqft",
        "coverage": ["Denver"],
        "requires_county_param": False,
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Colorado — Jefferson County ────────────────────────────────────────
    "CO_JEFFERSON": {
        "name": "Colorado (Jefferson County)",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://maps.jeffco.us/arcgis/rest/services"
            "/Property/Parcels/MapServer/0"
        ),
        "town_id_field": None,
        "town_id_type": "none",
        "calc_crs": "EPSG:2876",
        "field_map": {
            "parcel_id":    "ACCOUNTNO",
            "address":      "SITUS",
            "owner_name":   "OWNER",
            "zoning_code":  "ZONING",
            "lot_size":     "ACRES",
            "use_code":     "USE_CODE",
        },
        "lot_size_unit": "acres",
        "coverage": ["Jefferson County"],
        "requires_county_param": False,
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Washington — King County (Seattle area) ───────────────────────────
    "WA_KING": {
        "name": "Washington (King County)",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://gismaps.kingcounty.gov/arcgis/rest/services"
            "/Property/KingCo_Parcels/MapServer/0"
        ),
        "town_id_field": "CTYNAME",
        "town_id_type": "string",
        "calc_crs": "EPSG:2926",
        "field_map": {
            "parcel_id":    "PIN",
            "address":      "ADDR_FULL",
            "owner_name":   "TAXPAYER",
            "zoning_code":  "PREUSE_DESC",
            "lot_size":     "LOTSQFT",
            "use_code":     "PREUSE_CODE",
            "bld_area":     "SQFT_TOT_LIVING",
        },
        "lot_size_unit": "sqft",
        "coverage": ["King County", "Seattle", "Bellevue", "Redmond", "Kirkland"],
        "requires_county_param": False,
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Texas — Travis County (Austin) ────────────────────────────────────
    "TX_TRAVIS": {
        "name": "Texas (Travis County)",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://services.arcgis.com/0L95CJ0VTaxqcmED/arcgis/rest/services"
            "/TCAD_Parcels_2024/FeatureServer/0"
        ),
        "town_id_field": "CITY",
        "town_id_type": "string",
        "calc_crs": "EPSG:2277",
        "field_map": {
            "parcel_id":    "PROP_ID",
            "address":      "SITUS",
            "owner_name":   "OWNER_NAME",
            "zoning_code":  "STATE_CD",
            "lot_size":     "LAND_ACRES",
            "use_code":     "STATE_CD",
        },
        "lot_size_unit": "acres",
        "coverage": ["Travis County", "Austin"],
        "requires_county_param": False,
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Texas — Harris County (Houston) ───────────────────────────────────
    "TX_HARRIS": {
        "name": "Texas (Harris County)",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://arcweb.hcad.org/server/rest/services"
            "/public/parcels/MapServer/0"
        ),
        "town_id_field": "CITY",
        "town_id_type": "string",
        "calc_crs": "EPSG:2278",
        "field_map": {
            "parcel_id":    "HCAD_NUM",
            "address":      "SITE_ADDR",
            "owner_name":   "OWNER",
            "zoning_code":  "STATE_CLASS",
            "lot_size":     "ACREAGE",
            "use_code":     "STATE_CLASS",
        },
        "lot_size_unit": "acres",
        "coverage": ["Harris County", "Houston"],
        "requires_county_param": False,
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Oregon (statewide ORMAP) ──────────────────────────────────────────
    "OR": {
        "name": "Oregon",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://gis.oregon.gov/arcgis/rest/services"
            "/Framework/Parcels_Taxlots/MapServer/0"
        ),
        "town_id_field": "COUNTY",
        "town_id_type": "string",
        "calc_crs": "EPSG:2994",
        "field_map": {
            "parcel_id":    "MAPTAXLOT",
            "address":      "SITEADDR",
            "owner_name":   "OWNER1",
            "zoning_code":  "PROP_CODE",
            "lot_size":     "ACRES",
            "use_code":     "PROP_CODE",
        },
        "lot_size_unit": "acres",
        "coverage": ["statewide"],
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── North Carolina (statewide) ────────────────────────────────────────
    "NC": {
        "name": "North Carolina",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://services.nconemap.gov/secure/rest/services"
            "/NC1Map_Parcels/FeatureServer/0"
        ),
        "town_id_field": "COUNTY_DESC",
        "town_id_type": "string",
        "calc_crs": "EPSG:3358",
        "field_map": {
            "parcel_id":    "PARNO",
            "address":      "SITE_ADDR",
            "owner_name":   "OWNNAME",
            "zoning_code":  "LAND_CLASS",
            "lot_size":     "ACRES",
            "use_code":     "LAND_CLASS",
        },
        "lot_size_unit": "acres",
        "coverage": ["statewide"],
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Georgia — Fulton County (Atlanta) ─────────────────────────────────
    "GA_FULTON": {
        "name": "Georgia (Fulton County)",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://gisdata.fultoncountyga.gov/arcgis/rest/services"
            "/Parcels/MapServer/0"
        ),
        "town_id_field": "CITY",
        "town_id_type": "string",
        "calc_crs": "EPSG:2240",
        "field_map": {
            "parcel_id":    "PARCEL_ID",
            "address":      "SITEADDR",
            "owner_name":   "OWNER",
            "zoning_code":  "ZONING",
            "lot_size":     "ACRES",
            "use_code":     "LAND_USE",
        },
        "lot_size_unit": "acres",
        "coverage": ["Fulton County", "Atlanta"],
        "requires_county_param": False,
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Pennsylvania (statewide PASDA) ───────────────────────────────────
    "PA": {
        "name": "Pennsylvania",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://gis.penndot.pa.gov/arcgis/rest/services"
            "/Base/Cadastral/MapServer/0"
        ),
        "town_id_field": "MUNICIPALITY",
        "town_id_type": "string",
        "calc_crs": "EPSG:3364",             # PA State Plane South
        "field_map": {
            "parcel_id":    "PARCEL_ID",
            "address":      "SITEADDR",
            "owner_name":   "OWNER",
            "zoning_code":  "CLASSCODE",
            "lot_size":     "ACRES",
            "use_code":     "CLASSCODE",
            "bld_area":     "BLDG_SQFT",
        },
        "lot_size_unit": "acres",
        "coverage": ["statewide"],
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Illinois — Cook County (Chicago) ──────────────────────────────────
    "IL_COOK": {
        "name": "Illinois (Cook County)",
        "parcel_source": "arcgis_rest",
        # Cook County open parcel data (property index)
        "parcel_service_url": (
            "https://gisservices.cookcountyil.gov/arcgis/rest/services"
            "/Addresses/Parcels/MapServer/0"
        ),
        "town_id_field": "MUNICIPALITY",
        "town_id_type": "string",
        "calc_crs": "EPSG:3435",            # Illinois State Plane East
        "field_map": {
            "parcel_id":    "PIN14",        # 14-digit parcel index number
            "address":      "PROP_ADDR",    # property address
            "owner_name":   "TAXPAYER",     # taxpayer name
            "zoning_code":  "CLASS",        # Cook County property class
            "lot_size":     "LAND_SQFT",    # land area in square feet
            "use_code":     "CLASS",
            "bld_area":     "BLDG_SQFT",
        },
        "lot_size_unit": "sqft",
        "coverage": ["Cook County", "Chicago"],
        "requires_county_param": False,
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Minnesota — Hennepin County (Minneapolis) ──────────────────────────
    "MN_HENNEPIN": {
        "name": "Minnesota (Hennepin County)",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://gis.hennepin.us/arcgis/rest/services"
            "/HennepinData/PROPERTY/MapServer/0"
        ),
        "town_id_field": "MUNI_NAME",
        "town_id_type": "string",
        "calc_crs": "EPSG:26915",           # UTM Zone 15N
        "field_map": {
            "parcel_id":    "PID",
            "address":      "ANOKA_ADD",
            "owner_name":   "OWNER_NAME",
            "zoning_code":  "HOMESTEAD",
            "lot_size":     "SHAPE_AREA",   # from geometry (sq ft in state plane)
            "use_code":     "USE1_DESC",
        },
        "lot_size_unit": "sqft",
        "coverage": ["Hennepin County", "Minneapolis"],
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Michigan — Wayne County (Detroit) ─────────────────────────────────
    "MI_WAYNE": {
        "name": "Michigan (Wayne County)",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://gis.waynecounty.com/arcgis/rest/services"
            "/MIWC_ParcelsPublic/MapServer/0"
        ),
        "town_id_field": "CITY",
        "town_id_type": "string",
        "calc_crs": "EPSG:2252",            # Michigan State Plane South
        "field_map": {
            "parcel_id":    "PNUM",
            "address":      "ADDRESS",
            "owner_name":   "OWNER",
            "zoning_code":  "PROP_CLASS",
            "lot_size":     "TOTAL_ACRE",
            "use_code":     "PROP_CLASS",
        },
        "lot_size_unit": "acres",
        "coverage": ["Wayne County", "Detroit"],
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },

    # ── Arizona — Maricopa County (Phoenix) ───────────────────────────────
    "AZ_MARICOPA": {
        "name": "Arizona (Maricopa County)",
        "parcel_source": "arcgis_rest",
        "parcel_service_url": (
            "https://maps.maricopa.gov/arcgis/rest/services"
            "/Public/Parcels/MapServer/0"
        ),
        "town_id_field": "CITY",
        "town_id_type": "string",
        "calc_crs": "EPSG:2223",            # Arizona State Plane Central
        "field_map": {
            "parcel_id":    "APN",
            "address":      "SITE_ADDR",
            "owner_name":   "OWNER_NAME",
            "zoning_code":  "ZONING",
            "lot_size":     "LOT_SQFT",
            "use_code":     "USE_CODE",
            "bld_area":     "BLDG_SF",
        },
        "lot_size_unit": "sqft",
        "coverage": ["Maricopa County", "Phoenix", "Scottsdale", "Mesa"],
        "NEEDS_VALIDATION": True,
        "status": "beta",
    },
}


# Standard residential use codes shared across states
# Each state adapter maps its local codes to these internal types
STANDARD_USE_CODE_MAP = {
    # ── MA 3/4-digit use codes ─────────────────────────────────────────
    "101": "single_family", "1010": "single_family",
    "102": "condominium",   "1020": "condominium",
    "103": "mobile_home",   "1030": "mobile_home",
    "104": "two_family",    "1040": "two_family",
    "105": "three_family",  "1050": "three_family",
    "109": "single_family", "1090": "single_family",
    "111": "apartment_4_8",   "1110": "apartment_4_8",
    "112": "apartment_9_plus","1120": "apartment_9_plus",
    "113": "apartment_9_plus","1130": "apartment_9_plus",
    "121": "farmland",   "130": "commercial",
    "300": "industrial", "400": "mixed_use",
    "500": "recreational","600": "exempt",
    "700": "vacant",     "900": "undevelopable",

    # ── VT CAT (Category) codes ────────────────────────────────────────
    "R1":  "single_family",     # Residential 1 (primary)
    "R2":  "two_family",        # Residential 2 (secondary/rental)
    "CA":  "multi_family",      # Commercial apartments
    "M":   "multi_family",      # Multi-family
    "MHL": "mobile_home",       # Mobile home land
    "MHU": "mobile_home",       # Mobile home unit
    "S1":  "single_family",     # Seasonal 1
    "S2":  "single_family",     # Seasonal 2
    "F":   "farmland",          # Farm
    "C":   "commercial",        # Commercial
    "I":   "industrial",        # Industrial
    "UE":  "exempt",            # Utility electric
    "UO":  "exempt",            # Utility other
    "W":   "vacant",            # Woodland
    "O":   "exempt",            # Other

    # ── NH SLU (State Land Use) codes ──────────────────────────────────
    "11":  "single_family",     # Single family
    "12":  "multi_family",      # Multi-family
    "13":  "single_family",     # Farm/forest residential
    "14":  "mobile_home",       # Manufactured housing
    "17":  "single_family",     # Other improved residential
    "18":  "single_family",     # Seasonal / camp
    "19":  "single_family",     # Other residential
    "21":  "commercial",        # Grocery/convenience
    "22":  "commercial",        # Retail
    "23":  "commercial",        # Eating/drinking
    "24":  "commercial",        # Service station
    "25":  "commercial",        # Vehicle sales
    "26":  "commercial",        # Other retail
    "27":  "mixed_use",         # Multi-use (commercial + residential)
    "28":  "commercial",        # Recreation commercial
    "29":  "commercial",        # Other commercial
    "33":  "commercial",        # Office
    "34":  "commercial",        # Office condo
    "37":  "commercial",        # Health care
    "38":  "exempt",            # Government
    "39":  "commercial",        # Other service
    "40":  "industrial",        # Manufacturing
    "41":  "industrial",        # Manufacturing
    "42":  "industrial",        # Manufacturing
    "43":  "industrial",        # Warehouse
    "44":  "industrial",        # Warehouse
    "50":  "exempt",            # Institutional
    "51":  "exempt",            # Religious
    "52":  "exempt",            # Educational
    "53":  "exempt",            # Government
    "57":  "exempt",            # Church/exempt
    "60":  "exempt",            # Utility
    "70":  "vacant",            # Undeveloped land
    "71":  "vacant",            # Undeveloped land
    "72":  "farmland",          # Forest
    "73":  "farmland",          # Farmland
    "74":  "undevelopable",     # Wetland
    "75":  "undevelopable",     # Flood zone
    "80":  "recreational",      # Recreation/conservation
    "90":  "exempt",            # Other

    # ── NYC Tax Class codes (1-2 digit, used in NYC boroughs) ────────
    "01": "single_family",      # 1-3 family residential
    "1":  "single_family",
    "02": "multi_family",       # 4+ family / co-op
    "2":  "multi_family",
    "05": "condominium",        # Condos (various)
    "5":  "condominium",
    "06": "condominium",        # Residential condos
    "6":  "condominium",
    "08": "mixed_use",          # Mixed use
    "8":  "mixed_use",
    "11": "condominium",        # Residential condos
    "03": "exempt",             # Utility
    "04": "commercial",         # Commercial/industrial
    "07": "commercial",         # Commercial condos
    "09": "exempt",             # Utility condos
    "10": "vacant",             # Vacant land

    # ── NY Property Class codes (3-digit) ──────────────────────────────
    "210": "single_family",     # 1-family residential
    "215": "single_family",     # 1-family (with accessory use)
    "220": "two_family",        # 2-family residential
    "230": "multi_family",      # 3-family residential
    "240": "multi_family",      # Rural/community residence
    "250": "multi_family",      # Estate
    "260": "multi_family",      # Seasonal
    "270": "mobile_home",       # Mobile home
    "280": "multi_family",      # Multiple residences
    "281": "multi_family",      # Multiple (primarily 1-family)
    "283": "multi_family",      # Residence with commercial
    "311": "vacant",            # Vacant residential land
    "312": "vacant",            # Vacant residential land (<10 ac)
    "314": "vacant",            # Vacant rural residential
    "322": "vacant",            # Vacant rural
    "330": "vacant",            # Vacant commercial
    "340": "vacant",            # Vacant industrial
    "400": "commercial",        # Commercial (general)
    "411": "commercial",        # Apartment (5+ units)
    "421": "commercial",        # Shopping
    "432": "commercial",        # Gas station
    "449": "commercial",        # Other storage
    "464": "commercial",        # Office
    "480": "commercial",        # Multiple use
    "485": "mixed_use",         # Mixed res/commercial
    "500": "recreational",      # Recreation
    "600": "exempt",            # Community services
    "620": "exempt",            # Religious
    "651": "exempt",            # Government
    "695": "exempt",            # Cemetery
    "710": "industrial",        # Manufacturing
    "720": "industrial",        # Mining/quarry
    "800": "exempt",            # Public service
    "900": "exempt",            # Conservation/forest

    # ── NJ Property Class codes ────────────────────────────────────────
    "1":   "vacant",            # Vacant land
    "2":   "single_family",     # Residential
    "3A":  "farmland",          # Farm (regular)
    "3B":  "farmland",          # Farm (qualified)
    "4A":  "commercial",        # Commercial
    "4B":  "commercial",        # Industrial
    "4C":  "commercial",        # Apartment (5+ units)
    "5A":  "exempt",            # Railroad
    "5B":  "exempt",            # Utility
    "15A": "exempt",            # Public property
    "15B": "exempt",            # Public property
    "15C": "exempt",            # Public property
    "15D": "exempt",            # Public exempt
    "15E": "exempt",            # Other exempt
    "15F": "exempt",            # Other exempt
}


def get_state_config(state_abbr: str) -> dict | None:
    """Get the GIS config for a state."""
    return STATE_REGISTRY.get(state_abbr.upper())


def list_supported_states() -> list[dict]:
    """Return all states with their support status."""
    return [
        {
            "state": k,
            "name": v["name"],
            "status": v.get("status", "unknown"),
        }
        for k, v in STATE_REGISTRY.items()
    ]


if __name__ == "__main__":
    for s in list_supported_states():
        print(f"  {s['state']} — {s['name']} [{s['status']}]")
