"""
Municipality Resolver
Converts any US address or zip code into structured municipality metadata
using the free US Census Geocoder and TIGERweb APIs.

No API key required. Works for any US address.

Output:
    {
        "municipality_id": "ma_acton",
        "municipality_name": "Acton",
        "state": "MA",
        "state_fips": "25",
        "county": "Middlesex",
        "county_fips": "017",
        "place_fips": "00100",
        "lat": 42.4851,
        "lon": -71.4328,
        "input": "original input",
    }
"""

import json
import time
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote
from urllib.error import URLError, HTTPError


# ---------------------------------------------------------------------------
# Census Geocoder
# ---------------------------------------------------------------------------

CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/address"
CENSUS_ZIP_URL = "https://geocoding.geo.census.gov/geocoder/geographies/address"
CENSUS_ONESHOT_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"


def _fetch_json(url: str, timeout: int = 30) -> dict:
    req = Request(url, headers={"User-Agent": "PlinthSIP/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _geocode_address(address: str) -> dict | None:
    """Use Census geocoder to resolve a full address."""
    params = urlencode({
        "address": address,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "all",
        "format": "json",
    })
    url = f"{CENSUS_ONESHOT_URL}?{params}"
    try:
        data = _fetch_json(url)
        matches = data.get("result", {}).get("addressMatches", [])
        if not matches:
            return None
        return matches[0]
    except Exception:
        return None


def _geocode_zip(zip_code: str) -> dict | None:
    """Geocode a zip code by using a dummy street address with the zip."""
    # Census geocoder doesn't do zip-only — use "1 Main St, {zip}" as a proxy
    # to land inside the zip code area and extract municipality metadata.
    dummy_addresses = [
        f"1 Main St, {zip_code}",
        f"100 Main St, {zip_code}",
        f"1 Center St, {zip_code}",
    ]
    for addr in dummy_addresses:
        match = _geocode_address(addr)
        if match:
            return match
    return None


def _extract_municipality_from_match(match: dict) -> dict | None:
    """Extract municipality metadata from a Census geocoder match."""
    coords = match.get("coordinates", {})
    lat = coords.get("y")
    lon = coords.get("x")

    geographies = match.get("geographies", {})

    # Get state info
    states = geographies.get("States", [])
    if not states:
        return None
    state_info = states[0]
    state_fips = state_info.get("STATEFP", "")
    state_abbr = state_info.get("STUSAB", "")

    # Get county info
    counties = geographies.get("Counties", [])
    county_name = counties[0].get("NAME", "") if counties else ""
    county_fips = counties[0].get("COUNTYFP", "") if counties else ""

    # Get incorporated place (municipality) if available
    places = geographies.get("Incorporated Places", [])
    cousubs = geographies.get("County Subdivisions", [])

    municipality_name = ""
    place_fips = ""

    if places:
        municipality_name = places[0].get("NAME", "")
        place_fips = places[0].get("PLACEFP", "")
    elif cousubs:
        # County subdivision (township, town) for states without incorporated places
        municipality_name = cousubs[0].get("NAME", "")
        place_fips = cousubs[0].get("COUSUBFP", "")

    if not municipality_name:
        # Fall back to matched address city
        addr = match.get("matchedAddress", "")
        parts = addr.split(",")
        if len(parts) >= 2:
            municipality_name = parts[-3].strip() if len(parts) >= 3 else parts[-2].strip()

    # Build a clean municipality_id
    muni_slug = municipality_name.lower().replace(" ", "_").replace("'", "").replace("-", "_")
    municipality_id = f"{state_abbr.lower()}_{muni_slug}"

    return {
        "municipality_id": municipality_id,
        "municipality_name": municipality_name,
        "state": state_abbr,
        "state_fips": state_fips,
        "county": county_name,
        "county_fips": county_fips,
        "place_fips": place_fips,
        "lat": lat,
        "lon": lon,
        "matched_address": match.get("matchedAddress", ""),
    }


def _resolve_city_state(input_str: str) -> dict | None:
    """
    Resolve a 'City, ST' input by geocoding with a dummy street address.
    The Census geocoder needs a street address — we use '1 Main St' as a proxy
    to land inside the municipality, then extract municipality metadata.
    """
    import re
    # Match patterns like "Burlington, VT" or "Concord NH" or "Burlington, Vermont"
    m = re.match(r'^([A-Za-z\s\.\'-]+?)\s*,?\s+([A-Z]{2}|[A-Za-z]+)$', input_str.strip())
    if not m:
        return None

    city = m.group(1).strip()
    state = m.group(2).strip()

    # Try geocoding with a dummy address to land inside the municipality
    dummy_addresses = [
        f"1 Main St, {city}, {state}",
        f"100 Main St, {city}, {state}",
        f"1 Center St, {city}, {state}",
    ]
    for addr in dummy_addresses:
        match = _geocode_address(addr)
        if match:
            return match

    return None


def _resolve_zip_tigerweb(zip_code: str) -> dict | None:
    """Resolve a zip code via TIGERweb ZCTA centroid lookup."""
    url = (
        "https://tigerweb.geo.census.gov/arcrest/rest/services/TIGERweb/"
        "tigerWMS_Current/MapServer/2/query?"
        + urlencode({
            "where": f"ZCTA5CE20='{zip_code}'",
            "outFields": "ZCTA5CE20,CENTLAT,CENTLON",
            "f": "json",
            "returnGeometry": "false",
        })
    )
    try:
        data = _fetch_json(url)
        features = data.get("features", [])
        if not features:
            return None
        attrs = features[0].get("attributes", {})
        lat = float(attrs.get("CENTLAT", 0))
        lon = float(attrs.get("CENTLON", 0))
        if lat == 0 and lon == 0:
            return None
        # Now reverse-geocode via a dummy address near centroid
        # Use Census geocoder with coordinates
        addr = f"{lat}, {lon}"
        return _geocode_address(f"1 Main St, {zip_code}")
    except Exception:
        return None


def reverse_geocode(lat: float, lon: float) -> dict | None:
    """
    Reverse-geocode lat/lng to municipality metadata using the Census
    geocoder's coordinates endpoint. No API key required.
    """
    params = urlencode({
        "x": lon,
        "y": lat,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "all",
        "format": "json",
    })
    url = f"https://geocoding.geo.census.gov/geocoder/geographies/coordinates?{params}"
    try:
        data = _fetch_json(url, timeout=30)
        result = data.get("result", {})
        geographies = result.get("geographies", {})
        if not geographies:
            return None

        # Build a synthetic match dict that _extract_municipality_from_match expects
        match = {
            "coordinates": {"x": lon, "y": lat},
            "geographies": geographies,
            "matchedAddress": "",
        }
        return _extract_municipality_from_match(match)
    except Exception:
        return None


def resolve_municipality(input_str: str) -> dict | None:
    """
    Resolve any US address, city+state, or zip code to municipality metadata.

    Args:
        input_str: US address ("14 Main St, Burlington VT"),
                   city+state ("Burlington, VT"),
                   or zip code ("05401")

    Returns:
        Municipality metadata dict or None if not found.
    """
    input_str = input_str.strip()

    # Try as full address first
    match = _geocode_address(input_str)

    # If that fails, try as city+state (add dummy street address)
    if not match:
        match = _resolve_city_state(input_str)

    # If that fails and input looks like a zip, try zip approaches
    if not match and input_str.replace("-", "").isdigit():
        match = _geocode_zip(input_str)
        if not match:
            match = _resolve_zip_tigerweb(input_str)

    if not match:
        return None

    return _extract_municipality_from_match(match)


if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "14 Main St, Burlington VT"
    print(f"Resolving: {query}")
    result = resolve_municipality(query)
    if result:
        print(json.dumps(result, indent=2))
    else:
        print("Could not resolve municipality.")
