"""
Use code / property classification code → human-readable label lookup.

Covers:
  - NY State Real Property Classification Codes (100–999)
  - MA MassGIS Use Codes (4-digit, 1010–9999)
  - FL DOR Land Use Codes (01–99)
  - Generic fallbacks for other states

Usage:
    from app.engine.use_code_labels import get_use_label
    label = get_use_label("210")          # "One-Family Year-Round Residence"
    display = get_use_display("210")      # "210 — One-Family Year-Round Residence"
"""

# ─────────────────────────────────────────────────────────────
# NY State Real Property Classification Codes
# Source: NYS ORPS Real Property Classification Codes
# ─────────────────────────────────────────────────────────────
NY_USE_CODES: dict[str, str] = {
    # 100 – Agricultural
    "100": "Agricultural — General",
    "101": "Cropland, Field Crops",
    "102": "Cropland, Vegetable Crops",
    "103": "Cropland, Orchard/Vineyard",
    "105": "Truck Crops — Mucklands",
    "110": "Livestock",
    "111": "Livestock — Beef Cattle",
    "112": "Livestock — Dairy",
    "113": "Livestock — Poultry",
    "114": "Livestock — Sheep, Goats, Hogs",
    "120": "Field Crops",
    "140": "Truck Crops",
    "150": "Orchard/Vineyard",
    "151": "Orchards",
    "152": "Vineyards",
    "160": "Other Agricultural",
    "170": "Nursery/Greenhouse",
    "180": "Specialty Farms",
    "190": "Fish/Game/Forest",

    # 200 – Residential
    "210": "One-Family Year-Round Residence",
    "215": "One-Family Year-Round Residence w/ Accessory Apartment",
    "220": "Two-Family Year-Round Residence",
    "230": "Three-Family Year-Round Residence",
    "240": "Rural Residence with Acreage",
    "241": "Residence with Separate Garage",
    "242": "Residence with Pool",
    "250": "Estate",
    "260": "Seasonal Residence",
    "270": "Mobile Home",
    "271": "Multiple Mobile Homes",
    "280": "Multi-Family Residential",
    "281": "Multiple Residences on One Parcel",
    "282": "Mixed Residential",
    "283": "Residence with Incidental Commercial Use",
    "284": "High-Rise Apartment",

    # 300 – Vacant Land
    "300": "Vacant Land — General",
    "311": "Residential Vacant — Improved",
    "312": "Residential Vacant — Land Only",
    "314": "Rural Vacant — Under 10 Acres",
    "315": "Rural Vacant — 10+ Acres",
    "320": "Rural Vacant Land",
    "321": "Abandoned Agricultural",
    "322": "Swampland / Wetland",
    "323": "Other Rural Vacant",
    "330": "Industrial Vacant",
    "340": "Commercial Vacant",
    "341": "Commercial Vacant — Improved",
    "342": "Commercial Vacant — Land Only",
    "350": "Urban Vacant",
    "360": "Community Facilities Vacant",
    "380": "Public Utilities Vacant",

    # 400 – Commercial
    "400": "Commercial — General",
    "410": "Gas Station / Service Station",
    "411": "Gasoline Station",
    "412": "Auto Dealer / Sales",
    "420": "Motel / Hotel / Resort",
    "421": "Hotel",
    "422": "Motel",
    "423": "Resort / Inn",
    "430": "Motor Vehicle Service / Repair",
    "431": "Auto Repair",
    "440": "Storage / Warehousing",
    "441": "Storage Unit Facility",
    "450": "Retail",
    "452": "Shopping Center",
    "460": "Banks / Financial Institutions",
    "461": "Bank",
    "462": "Insurance Office",
    "464": "Office Building",
    "465": "Professional Office",
    "470": "Miscellaneous Commercial",
    "471": "Funeral Home",
    "472": "Car Wash",
    "473": "Greenhouse / Nursery Commercial",
    "480": "Multiple Use — Commercial Predominant",
    "481": "Mixed — Commercial / Residential",
    "482": "Mixed — Commercial / Industrial",
    "484": "One-Story Small Structure",
    "486": "Minimart / Convenience Store",

    # 500 – Recreation & Entertainment
    "500": "Recreation / Entertainment — General",
    "510": "Amusement / Theme Park",
    "520": "Arena / Stadium",
    "521": "Marina / Boat Launch",
    "530": "Outdoor Recreation",
    "531": "Camp / Campground",
    "532": "Golf Course",
    "534": "Skating Rink",
    "540": "Social Organizations",
    "541": "Private Club",
    "542": "VFW / American Legion Hall",
    "550": "Amusements",
    "560": "Indoor Facilities",
    "570": "Hunting / Fishing Clubs",
    "580": "Cultural Facilities",
    "581": "Museum",
    "582": "Theater / Performing Arts",

    # 600 – Community Services
    "600": "Community Services — General",
    "610": "Education",
    "611": "Elementary School",
    "612": "Middle / High School",
    "613": "University / College",
    "614": "Private School",
    "620": "Religious",
    "621": "Church / Place of Worship",
    "630": "Human Services",
    "632": "Social Services",
    "633": "Day Care Center",
    "640": "Health Care",
    "641": "Hospital",
    "642": "Nursing Home",
    "650": "Governmental",
    "651": "Post Office",
    "652": "Police / Fire Station",
    "653": "Government Building",
    "660": "Cemeteries",

    # 700 – Industrial
    "700": "Industrial — General",
    "710": "Light Manufacturing",
    "714": "Light Industrial",
    "720": "Heavy Industrial",
    "730": "Mineral Extraction / Mining",
    "740": "Waste Disposal",
    "744": "Landfill",
    "750": "Industrial — Other",

    # 800 – Public Services
    "800": "Public Services — General",
    "810": "Water — Municipal",
    "822": "Electric Power",
    "826": "Gas Distribution",
    "830": "Communication",
    "840": "Transportation Services",
    "841": "Airports / Airfields",
    "842": "Railroad",
    "843": "Bus Terminal / Depot",
    "850": "Services",
    "870": "Solid Waste Facilities",

    # 900 – Wild, Forested, Conservation
    "900": "Forest / Wild / Conservation — General",
    "910": "Reforestation",
    "911": "Forest",
    "920": "Private Conservation",
    "930": "State — Forest / Wild",
    "931": "State Park",
    "932": "State Forest",
    "940": "Federal — Forest / Wild",
    "942": "National Park",
    "950": "Nature Conservancy",
    "960": "County — Forest / Wild",
    "961": "County Park",
    "971": "Wetlands — Tidal",
    "972": "Wetlands — Freshwater",
    "980": "Underwater Land",
    "990": "Taxable State Land — Other",
}

# ─────────────────────────────────────────────────────────────
# MA MassGIS Property Use Codes (4-digit)
# ─────────────────────────────────────────────────────────────
MA_USE_CODES: dict[str, str] = {
    "1010": "Single Family Residential",
    "1020": "Condominium",
    "1030": "Multi-Family (2–3 Units)",
    "1040": "Apartment (4–8 Units)",
    "1041": "Apartment (9+ Units)",
    "1050": "Assisted Living / Senior Housing",
    "1060": "Mobile Home",
    "1090": "Residential — Other",
    "1300": "Mixed Use — Residential/Commercial",
    "3010": "Retail Commercial",
    "3020": "Office",
    "3030": "Hotel / Motel",
    "3040": "Gas Station",
    "3100": "Mixed Use — Commercial",
    "4010": "Light Industrial",
    "4020": "Heavy Industrial",
    "5010": "Agricultural — Active",
    "5030": "Forest / Woodland",
    "9010": "Exempt — Municipal",
    "9020": "Exempt — State",
    "9030": "Exempt — Federal",
    "9060": "Exempt — Religious",
    "9070": "Exempt — Educational",
    "1110": "Vacant Residential Land",
    "1310": "Vacant Commercial Land",
    "1330": "Vacant Industrial Land",
}

# ─────────────────────────────────────────────────────────────
# FL DOR Land Use Codes (2-digit)
# ─────────────────────────────────────────────────────────────
FL_USE_CODES: dict[str, str] = {
    "00": "Vacant Residential",
    "01": "Single Family Residence",
    "02": "Mobile Home",
    "03": "Multi-Family (10+ Units)",
    "04": "Condominium",
    "05": "Cooperatives",
    "06": "Retirement / Senior Residence",
    "07": "Miscellaneous Residential",
    "08": "Multi-Family (2–9 Units)",
    "09": "Planned Unit Development (PUD)",
    "10": "Vacant Commercial",
    "11": "Stores",
    "12": "Mixed Use Residential / Commercial",
    "13": "Department Store",
    "14": "Supermarket",
    "15": "Regional Mall",
    "16": "Restaurant / Cafeteria",
    "17": "Drive-In Restaurant",
    "18": "Auto Dealer / Sales",
    "19": "Repair Service Shop",
    "20": "Grocery Store / Drug Store",
    "21": "Garage / Service Station",
    "22": "Automotive Service",
    "23": "Financial Institution",
    "24": "Insurance Company",
    "25": "Repair Shop",
    "26": "Service Station",
    "27": "Auto Repair / Service",
    "28": "Mobile Home Park",
    "29": "Wholesale",
    "30": "Florist / Greenhouse",
    "31": "Drive-In Theater",
    "32": "Enclosed Theater",
    "33": "Night Club / Bar",
    "34": "Bowling Alley / Skating Rink",
    "38": "Golf Course",
    "39": "Hotel / Motel",
    "40": "Vacant Industrial",
    "41": "Light Manufacturing",
    "42": "Heavy Industrial",
    "48": "Warehousing / Storage",
    "50": "Agricultural",
    "51": "Cropland — Irrigated",
    "69": "Ornamentals / Misc. Agriculture",
    "70": "Vacant Institutional",
    "71": "Churches / Religious",
    "72": "Private Schools",
    "73": "Private Hospitals",
    "74": "Homes for the Aged",
    "75": "Orphanages / Non-Profits",
    "80": "Governmental — Municipal",
    "81": "Military",
    "82": "Forest / Parks — State",
    "86": "Counties",
    "89": "Federal Government",
    "90": "Leasehold Interests",
    "99": "Acreage Not Agriculture",
}

# ─────────────────────────────────────────────────────────────
# NJ Property Class Codes
# ─────────────────────────────────────────────────────────────
NJ_USE_CODES: dict[str, str] = {
    "1":  "Vacant Land",
    "2":  "Residential — Farm Assessed",
    "3A": "Farmland — Regular Assessment",
    "3B": "Farmland — Farm Assessed",
    "4A": "Commercial",
    "4B": "Industrial",
    "4C": "Apartment (4+ Units)",
    "5A": "Class I Railroad",
    "5B": "Class II Railroad",
    "6A": "Telephone / Telegraph",
    "6B": "Petroleum / Natural Gas",
    "15A": "Public School",
    "15B": "Other School",
    "15C": "Public Property — Other",
    "15D": "Church / Charitable",
    "15E": "Cemeteries",
    "15F": "Other Exempt",
}

# ─────────────────────────────────────────────────────────────
# Combined lookup (tries NY codes first, then MA, FL, NJ)
# ─────────────────────────────────────────────────────────────
_ALL_CODES: dict[str, str] = {
    **FL_USE_CODES,
    **NJ_USE_CODES,
    **MA_USE_CODES,
    **NY_USE_CODES,   # NY last so its values win on any collision
}


def get_use_label(code: str | None) -> str | None:
    """
    Return the human-readable label for a use/property-class code.
    Returns None if not found.
    """
    if not code:
        return None
    code = str(code).strip()
    return _ALL_CODES.get(code) or _ALL_CODES.get(code.lstrip("0")) or None


def get_use_display(code: str | None) -> str:
    """
    Return "CODE — Label" for display, or just the code if label not found.
    e.g. get_use_display("210") → "210 — One-Family Year-Round Residence"
    """
    if not code:
        return "—"
    label = get_use_label(code)
    if label:
        return f"{code} — {label}"
    return str(code)
