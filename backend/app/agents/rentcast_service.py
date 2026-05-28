"""
RentCast service — rent estimates + comparables for hypothetical ADU units.

RentCast's AVM Rent Estimate endpoint accepts a target address plus
description of a unit (beds/baths/sqft/property type) and returns an
estimated monthly rent, a low/high band, and a set of nearby
comparable listings. ADUs themselves are rare in their training set,
so we query the model as an "Apartment" of typical ADU dimensions.

Three preset specs cover the ADU size spectrum and form the standard
output of `get_adu_rent_estimates`:
  - studio: 450 sqft, 0 BR, 1 BA
  - 1BR:    650 sqft, 1 BR, 1 BA   ← Plinth default
  - 2BR:    900 sqft, 2 BR, 1 BA

API docs: https://developers.rentcast.io/reference/rent-estimate
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings


RENTCAST_BASE = "https://api.rentcast.io/v1"
RENTCAST_RENT_AVM = f"{RENTCAST_BASE}/avm/rent/long-term"

# ADU-typical premium over apartment AVM. RentCast trains on apartments,
# but detached/private ADUs tend to command a small premium. Conservative
# default; expose as a knob via the endpoint if needed later.
ADU_PREMIUM = 1.05

# Default specs we query for an ADU calculator output. Sizes chosen to
# match typical ADU footprints (Plinth standard 1BR is ~525-650 sqft).
ADU_SPECS: list[dict[str, Any]] = [
    {"key": "studio", "label": "Studio",  "bedrooms": 0, "bathrooms": 1, "squareFootage": 450},
    {"key": "oneBr",  "label": "1 BR",    "bedrooms": 1, "bathrooms": 1, "squareFootage": 650},
    {"key": "twoBr",  "label": "2 BR",    "bedrooms": 2, "bathrooms": 1, "squareFootage": 900},
]


@dataclass
class Comparable:
    address: str
    bedrooms: int | None
    bathrooms: float | None
    square_footage: int | None
    rent_monthly: float | None
    distance_mi: float | None
    days_on_market: int | None
    correlation: float | None


@dataclass
class RentEstimate:
    spec_key: str            # "studio" | "oneBr" | "twoBr" | custom
    spec_label: str          # human label
    bedrooms: int
    bathrooms: float
    square_footage: int
    rent: float | None       # point estimate (post-premium)
    rent_low: float | None
    rent_high: float | None
    rent_psf_month: float | None
    comparables: list[Comparable] = field(default_factory=list)
    error: str | None = None


class RentCastError(Exception):
    pass


def _ensure_key() -> str:
    key = settings.RENTCAST_API_KEY
    if not key:
        raise RentCastError(
            "RENTCAST_API_KEY is not set. Add it to backend/.env to enable rent estimates."
        )
    return key


def _apply_premium(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * ADU_PREMIUM, 2)


def _parse_comparables(raw: list[dict] | None) -> list[Comparable]:
    if not raw:
        return []
    out: list[Comparable] = []
    for c in raw[:10]:
        out.append(
            Comparable(
                address=c.get("formattedAddress") or c.get("addressLine1") or "—",
                bedrooms=c.get("bedrooms"),
                bathrooms=c.get("bathrooms"),
                square_footage=c.get("squareFootage"),
                rent_monthly=c.get("price"),
                distance_mi=c.get("distance"),
                days_on_market=c.get("daysOnMarket"),
                correlation=c.get("correlation"),
            )
        )
    return out


def get_rent_estimate(
    address: str,
    bedrooms: int,
    bathrooms: float,
    square_footage: int,
    spec_key: str = "custom",
    spec_label: str = "Custom",
    property_type: str = "Apartment",
    apply_adu_premium: bool = True,
    timeout: float = 20.0,
) -> RentEstimate:
    """
    Single RentCast AVM call for one (address, spec) pair.

    Returns a RentEstimate with `error` populated on failure rather than
    raising — callers are usually multi-spec aggregators and shouldn't
    short-circuit on a single bad spec.
    """
    api_key = _ensure_key()

    params = {
        "address": address,
        "propertyType": property_type,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "squareFootage": square_footage,
        "compCount": 5,
    }
    headers = {
        "X-Api-Key": api_key,
        "Accept": "application/json",
        "User-Agent": "PlinthSIP/1.0 (rentcast_service)",
    }

    base = RentEstimate(
        spec_key=spec_key,
        spec_label=spec_label,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        square_footage=square_footage,
        rent=None,
        rent_low=None,
        rent_high=None,
        rent_psf_month=None,
    )

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(RENTCAST_RENT_AVM, params=params, headers=headers)
        if r.status_code == 404:
            base.error = "No rent data available for this address from RentCast."
            return base
        if r.status_code >= 400:
            base.error = f"RentCast error {r.status_code}: {r.text[:200]}"
            return base
        data = r.json()
    except httpx.RequestError as e:
        base.error = f"RentCast request failed: {e}"
        return base
    except ValueError:
        base.error = "RentCast returned non-JSON response."
        return base

    rent = data.get("rent")
    low = data.get("rentRangeLow")
    high = data.get("rentRangeHigh")

    if apply_adu_premium:
        rent = _apply_premium(rent)
        low = _apply_premium(low)
        high = _apply_premium(high)

    base.rent = rent
    base.rent_low = low
    base.rent_high = high
    base.rent_psf_month = (rent / square_footage) if (rent and square_footage) else None
    base.comparables = _parse_comparables(data.get("comparables"))
    return base


def get_adu_rent_estimates(
    address: str,
    apply_adu_premium: bool = True,
) -> list[RentEstimate]:
    """
    Run the three standard ADU specs (studio / 1BR / 2BR) against an
    address. Always returns three RentEstimate objects (some may have
    `error` set if a spec failed).
    """
    out: list[RentEstimate] = []
    for spec in ADU_SPECS:
        est = get_rent_estimate(
            address=address,
            bedrooms=spec["bedrooms"],
            bathrooms=spec["bathrooms"],
            square_footage=spec["squareFootage"],
            spec_key=spec["key"],
            spec_label=spec["label"],
            apply_adu_premium=apply_adu_premium,
        )
        out.append(est)
    return out


def estimate_to_dict(est: RentEstimate) -> dict:
    """Serialize a RentEstimate to a plain JSON-safe dict for API responses."""
    return {
        "spec_key": est.spec_key,
        "spec_label": est.spec_label,
        "bedrooms": est.bedrooms,
        "bathrooms": est.bathrooms,
        "square_footage": est.square_footage,
        "rent": est.rent,
        "rent_low": est.rent_low,
        "rent_high": est.rent_high,
        "rent_psf_month": est.rent_psf_month,
        "comparables": [
            {
                "address": c.address,
                "bedrooms": c.bedrooms,
                "bathrooms": c.bathrooms,
                "square_footage": c.square_footage,
                "rent_monthly": c.rent_monthly,
                "distance_mi": c.distance_mi,
                "days_on_market": c.days_on_market,
                "correlation": c.correlation,
            }
            for c in est.comparables
        ],
        "error": est.error,
    }
