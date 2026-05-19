/**
 * Use code / property classification code → human-readable label lookup.
 * Mirrors the backend app/engine/use_code_labels.py so the UI shows the same labels.
 */

const NY_USE_CODES: Record<string, string> = {
  // 100 – Agricultural
  "100": "Agricultural",
  "101": "Cropland, Field Crops",
  "102": "Cropland, Vegetable Crops",
  "103": "Cropland, Orchard/Vineyard",
  "110": "Livestock",
  "111": "Beef Cattle",
  "112": "Dairy",
  "113": "Poultry",
  "120": "Field Crops",
  "140": "Truck Crops",
  "150": "Orchard/Vineyard",
  "170": "Nursery/Greenhouse",
  "190": "Fish/Game/Forest",

  // 200 – Residential
  "210": "One-Family Year-Round Residence",
  "215": "One-Family Residence w/ Accessory Apartment",
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

  // 300 – Vacant Land
  "300": "Vacant Land",
  "311": "Residential Vacant — Improved",
  "312": "Residential Vacant — Land Only",
  "314": "Rural Vacant — Under 10 Acres",
  "315": "Rural Vacant — 10+ Acres",
  "320": "Rural Vacant Land",
  "322": "Swampland / Wetland",
  "330": "Industrial Vacant",
  "340": "Commercial Vacant",
  "350": "Urban Vacant",

  // 400 – Commercial
  "400": "Commercial",
  "410": "Gas Station / Service Station",
  "411": "Gasoline Station",
  "412": "Auto Dealer / Sales",
  "420": "Motel / Hotel / Resort",
  "421": "Hotel",
  "422": "Motel",
  "430": "Motor Vehicle Service / Repair",
  "450": "Retail",
  "452": "Shopping Center",
  "460": "Banks / Financial Institutions",
  "464": "Office Building",
  "465": "Professional Office",
  "480": "Mixed Use — Commercial Predominant",
  "481": "Mixed — Commercial / Residential",
  "484": "One-Story Small Structure",
  "486": "Minimart / Convenience Store",

  // 500 – Recreation
  "500": "Recreation / Entertainment",
  "520": "Arena / Stadium",
  "521": "Marina / Boat Launch",
  "530": "Outdoor Recreation",
  "531": "Camp / Campground",
  "532": "Golf Course",
  "540": "Social Organizations",
  "541": "Private Club",
  "580": "Cultural Facilities",
  "581": "Museum",
  "582": "Theater / Performing Arts",

  // 600 – Community Services
  "600": "Community Services",
  "610": "Education",
  "611": "Elementary School",
  "612": "Middle / High School",
  "613": "University / College",
  "614": "Private School",
  "620": "Religious",
  "621": "Church / Place of Worship",
  "630": "Human Services",
  "633": "Day Care Center",
  "640": "Health Care",
  "641": "Hospital",
  "642": "Nursing Home",
  "650": "Government",
  "651": "Post Office",
  "652": "Police / Fire Station",
  "660": "Cemetery",

  // 700 – Industrial
  "700": "Industrial",
  "710": "Light Manufacturing",
  "714": "Light Industrial",
  "720": "Heavy Industrial",
  "730": "Mineral Extraction / Mining",
  "740": "Waste Disposal",
  "744": "Landfill",

  // 800 – Public Services
  "800": "Public Services",
  "810": "Municipal Water",
  "822": "Electric Power",
  "840": "Transportation",
  "841": "Airport / Airfield",
  "842": "Railroad",

  // 900 – Wild / Forest / Conservation
  "900": "Forest / Wild / Conservation",
  "910": "Reforestation",
  "911": "Forest",
  "920": "Private Conservation",
  "930": "State Forest / Wild",
  "931": "State Park",
  "932": "State Forest",
  "940": "Federal Forest / Wild",
  "942": "National Park",
  "950": "Nature Conservancy",
  "960": "County Park / Forest",
  "971": "Tidal Wetlands",
  "972": "Freshwater Wetlands",
  "980": "Underwater Land",
};

const MA_USE_CODES: Record<string, string> = {
  "1010": "Single Family Residential",
  "1020": "Condominium",
  "1030": "Multi-Family (2–3 Units)",
  "1040": "Apartment (4–8 Units)",
  "1041": "Apartment (9+ Units)",
  "1050": "Assisted Living / Senior Housing",
  "1060": "Mobile Home",
  "1090": "Residential — Other",
  "1110": "Vacant Residential Land",
  "1300": "Mixed Use — Residential/Commercial",
  "3010": "Retail Commercial",
  "3020": "Office",
  "3030": "Hotel / Motel",
  "4010": "Light Industrial",
  "4020": "Heavy Industrial",
  "5010": "Agricultural — Active",
  "5030": "Forest / Woodland",
  "9010": "Exempt — Municipal",
  "9020": "Exempt — State",
  "9060": "Exempt — Religious",
  "9070": "Exempt — Educational",
};

const FL_USE_CODES: Record<string, string> = {
  "00": "Vacant Residential",
  "01": "Single Family Residence",
  "02": "Mobile Home",
  "03": "Multi-Family (10+ Units)",
  "04": "Condominium",
  "05": "Cooperative",
  "06": "Retirement / Senior Residence",
  "07": "Miscellaneous Residential",
  "08": "Multi-Family (2–9 Units)",
  "09": "Planned Unit Development",
  "10": "Vacant Commercial",
  "11": "Retail Store",
  "39": "Hotel / Motel",
  "40": "Vacant Industrial",
  "41": "Light Manufacturing",
  "42": "Heavy Industrial",
  "48": "Warehousing / Storage",
  "50": "Agricultural",
  "71": "Religious",
  "72": "Private School",
  "73": "Private Hospital",
  "80": "Government — Municipal",
  "82": "State Forest / Park",
  "89": "Federal Government",
};

const ALL_CODES: Record<string, string> = {
  ...FL_USE_CODES,
  ...MA_USE_CODES,
  ...NY_USE_CODES,
};

/** Return the human-readable label for a use/property-class code, or null. */
export function getUseLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  const c = String(code).trim();
  return ALL_CODES[c] ?? ALL_CODES[c.replace(/^0+/, '')] ?? null;
}

/**
 * Return "CODE — Label" for display, or just the raw code if no label found.
 * e.g. getUseDisplay("210") → "210 — One-Family Year-Round Residence"
 */
export function getUseDisplay(code: string | null | undefined): string {
  if (!code) return '—';
  const label = getUseLabel(code);
  return label ? `${code} — ${label}` : code;
}
