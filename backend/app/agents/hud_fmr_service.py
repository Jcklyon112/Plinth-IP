"""
HUD Fair Market Rent fallback for addresses RentCast can't cover.

RentCast 404s in rural areas with no rental listings (Rangeley ME, parts
of the NEK in VT, the Adirondacks). HUD publishes county-level Fair
Market Rents for every county in the US — a useful floor estimate when
no market comps exist.

Lookup chain:
  1. Census geocoder: free, no auth → address -> county FIPS
  2. HUD FMR API: bearer token (free signup at huduser.gov) → FIPS -> FMR

FMR is conservative (HUD targets the 40th percentile of standard quality
rentals). For an ADU — typically new construction at higher finish — we
apply the same 1.05 ADU premium we use elsewhere, plus a small modern-
construction uplift (1.10) since FMR includes older inventory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.agents.rentcast_service import ADU_SPECS, ADU_PREMIUM, RentEstimate
from app.config import settings


CENSUS_GEOCODE_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
HUD_FMR_BASE = "https://www.huduser.gov/hudapi/public/fmr/data"

# FMR represents 40th-percentile standard-quality inventory. New-construction
# ADUs sit higher; we apply a modest uplift before the standard ADU premium.
HUD_MODERN_CONSTRUCTION_UPLIFT = 1.10


class HudFmrError(Exception):
    pass


@dataclass
class HudCountyLookup:
    state_fips: str  # 2 digits
    county_fips: str  # 3 digits
    county_name: str
    state_abbr: str


def _ensure_hud_token() -> str:
    token = settings.HUD_API_TOKEN
    if not token:
        raise HudFmrError(
            "HUD_API_TOKEN is not set. Sign up free at huduser.gov and add to backend/.env."
        )
    return token


def _geocode_to_county(address: str, timeout: float = 15.0) -> HudCountyLookup | None:
    """
    Resolve a free-text address to a county FIPS via the US Census geocoder.
    Returns None if no match. The Census geocoder is free, unlimited, no auth.
    """
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "Counties,States",
        "format": "json",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(CENSUS_GEOCODE_URL, params=params)
        if r.status_code != 200:
            return None
        payload = r.json()
    except (httpx.RequestError, ValueError):
        return None

    matches = payload.get("result", {}).get("addressMatches", [])
    if not matches:
        return None

    geos = matches[0].get("geographies", {})
    counties = geos.get("Counties", [])
    states = geos.get("States", [])
    if not counties:
        return None

    county = counties[0]
    state = states[0] if states else {}

    state_fips = str(county.get("STATE") or "").zfill(2)
    county_fips = str(county.get("COUNTY") or "").zfill(3)
    if not state_fips or not county_fips:
        return None

    return HudCountyLookup(
        state_fips=state_fips,
        county_fips=county_fips,
        county_name=str(county.get("BASENAME") or county.get("NAME") or "—"),
        state_abbr=str(state.get("STUSAB") or ""),
    )


def _hud_entity_id(lookup: HudCountyLookup) -> str:
    """HUD county entity_id format: {state_fips}{county_fips}99999 (10 digits)."""
    return f"{lookup.state_fips}{lookup.county_fips}99999"


def _fetch_hud_fmr(entity_id: str, year: int, timeout: float = 20.0) -> dict | None:
    token = _ensure_hud_token()
    url = f"{HUD_FMR_BASE}/{entity_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "PlinthAduRentCalculator/1.0",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url, params={"year": year}, headers=headers)
        if r.status_code != 200:
            return None
        return r.json()
    except (httpx.RequestError, ValueError):
        return None


def _extract_basicdata(payload: dict) -> dict | None:
    """
    HUD returns one of two shapes:
      - Standard FMR (most counties): payload.data.basicdata = {Efficiency: ..., One-Bedroom: ..., ...}
      - SAFMR (some metro ZIPs):       payload.data.basicdata = [{zip_code, Efficiency, ...}, ...]
    For SAFMR, we average across all ZIPs in the response (caller can't
    disambiguate ZIPs from a free-text address reliably enough).
    """
    data = payload.get("data") or {}
    bd = data.get("basicdata")
    if isinstance(bd, dict):
        return bd
    if isinstance(bd, list) and bd:
        # Average each rent bucket across the list
        keys = ["Efficiency", "One-Bedroom", "Two-Bedroom", "Three-Bedroom", "Four-Bedroom"]
        avg: dict[str, float] = {}
        for k in keys:
            vals = [row.get(k) for row in bd if isinstance(row.get(k), (int, float))]
            if vals:
                avg[k] = sum(vals) / len(vals)
        return avg or None
    return None


_FMR_KEY_BY_SPEC = {
    "studio": "Efficiency",
    "oneBr": "One-Bedroom",
    "twoBr": "Two-Bedroom",
}


def get_hud_fmr_estimates(
    address: str,
    apply_adu_premium: bool = True,
    year: int | None = None,
) -> tuple[list[RentEstimate], str | None]:
    """
    HUD FMR fallback. Returns (estimates, note) where estimates is the
    same 3-element list shape as get_adu_rent_estimates so the caller
    can swap it in transparently. `note` describes the data lineage
    (county + year) for display.

    Returns ([], error_message) if the lookup fails.
    """
    lookup = _geocode_to_county(address)
    if not lookup:
        return [], "Census geocoder could not resolve this address to a county."

    # HUD publishes FMR a year behind; use most-recent prior year if not specified.
    target_year = year or (datetime.now().year - 1)
    payload = _fetch_hud_fmr(_hud_entity_id(lookup), target_year)
    if payload is None:
        # Try one year earlier in case current year not yet published.
        payload = _fetch_hud_fmr(_hud_entity_id(lookup), target_year - 1)
        if payload is None:
            return [], f"HUD FMR has no data for {lookup.county_name}, {lookup.state_abbr}."
        target_year = target_year - 1

    basicdata = _extract_basicdata(payload)
    if not basicdata:
        return [], f"HUD FMR response missing rent data for {lookup.county_name}, {lookup.state_abbr}."

    estimates: list[RentEstimate] = []
    for spec in ADU_SPECS:
        fmr_key = _FMR_KEY_BY_SPEC.get(spec["key"])
        raw = basicdata.get(fmr_key) if fmr_key else None
        rent: float | None = float(raw) if isinstance(raw, (int, float)) else None

        if rent is not None:
            rent = rent * HUD_MODERN_CONSTRUCTION_UPLIFT
            if apply_adu_premium:
                rent = rent * ADU_PREMIUM
            rent = round(rent, 2)

        # HUD doesn't publish a range — synthesize ±15% band for display parity.
        rent_low = round(rent * 0.85, 2) if rent is not None else None
        rent_high = round(rent * 1.15, 2) if rent is not None else None
        rent_psf = (rent / spec["squareFootage"]) if (rent and spec["squareFootage"]) else None

        estimates.append(
            RentEstimate(
                spec_key=spec["key"],
                spec_label=spec["label"],
                bedrooms=spec["bedrooms"],
                bathrooms=spec["bathrooms"],
                square_footage=spec["squareFootage"],
                rent=rent,
                rent_low=rent_low,
                rent_high=rent_high,
                rent_psf_month=rent_psf,
                comparables=[],
                error=None if rent is not None else "HUD FMR missing this size bracket.",
            )
        )

    note = (
        f"HUD Fair Market Rent — {lookup.county_name} County, {lookup.state_abbr} "
        f"({target_year}). Modern-construction uplift ({int((HUD_MODERN_CONSTRUCTION_UPLIFT - 1) * 100)}%) "
        f"applied because FMR represents 40th-percentile standard inventory."
    )
    return estimates, note


_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def extract_zip(address: str) -> str | None:
    """Best-effort ZIP extraction for cache keying; not required for the lookup itself."""
    m = _ZIP_RE.search(address)
    return m.group(1) if m else None
