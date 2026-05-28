"""
Rent estimate router — single-spec query against RentCast with HUD FMR
fallback. Caller passes the chosen Plinth model's specs.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.hud_fmr_service import HudFmrError, get_hud_fmr_estimates
from app.agents.rentcast_service import (
    RentCastError,
    estimate_to_dict,
    get_rent_estimate,
)
from app.database import get_db
from app.models import RentEstimateCache, normalize_address_key

router = APIRouter()


CACHE_TTL = timedelta(days=7)


class RentRequest(BaseModel):
    address: str = Field(..., min_length=5, description="Full street address with city/state/ZIP")
    bedrooms: int = Field(..., ge=0, le=10)
    bathrooms: float = Field(..., ge=0.5, le=10)
    square_footage: int = Field(..., ge=100, le=10000)
    property_type: str = Field(default="Single Family")
    apply_adu_premium: bool = Field(default=False, description="Optional 5% premium over base AVM")
    model_label: str | None = Field(default=None, description="Plinth model label for echoing back, e.g. 'Model 02'")


def _cache_key(body: RentRequest) -> str:
    addr = normalize_address_key(body.address)
    spec = f"{body.property_type}|{body.bedrooms}b{body.bathrooms}ba{body.square_footage}sf|prem={int(body.apply_adu_premium)}"
    return f"{addr}|{spec}"


def _cache_get(db: Session, key: str) -> dict | None:
    row = db.query(RentEstimateCache).filter(RentEstimateCache.address_key == key).one_or_none()
    if row is None:
        return None
    if datetime.utcnow() - row.fetched_at > CACHE_TTL:
        db.delete(row)
        db.commit()
        return None
    return json.loads(row.payload_json)


def _cache_put(db: Session, key: str, payload: dict) -> None:
    existing = db.query(RentEstimateCache).filter(RentEstimateCache.address_key == key).one_or_none()
    if existing is not None:
        existing.payload_json = json.dumps(payload)
        existing.source = payload.get("source", "unknown")
        existing.fetched_at = datetime.utcnow()
    else:
        db.add(RentEstimateCache(
            address_key=key,
            source=payload.get("source", "unknown"),
            payload_json=json.dumps(payload),
        ))
    db.commit()


@router.post("/rent-estimate")
def rent_estimate(body: RentRequest, db: Session = Depends(get_db)):
    key = _cache_key(body)
    cached = _cache_get(db, key)
    if cached is not None:
        return cached

    # 1. Try RentCast for the chosen Plinth model's specs.
    rentcast_error: str | None = None
    try:
        est = get_rent_estimate(
            address=body.address,
            bedrooms=body.bedrooms,
            bathrooms=body.bathrooms,
            square_footage=body.square_footage,
            spec_key="model",
            spec_label=body.model_label or "Plinth Model",
            property_type=body.property_type,
            apply_adu_premium=body.apply_adu_premium,
        )
    except RentCastError as e:
        est = None
        rentcast_error = str(e)

    used_source = "rentcast"
    source_note: str | None = None

    # 2. Fall back to HUD FMR if RentCast couldn't price the model.
    if est is None or est.rent is None:
        try:
            hud_estimates, hud_note = get_hud_fmr_estimates(
                address=body.address,
                apply_adu_premium=body.apply_adu_premium,
            )
        except HudFmrError as e:
            detail = rentcast_error or (est.error if est else "No RentCast price available for this address.")
            raise HTTPException(
                status_code=503,
                detail=f"{detail} HUD fallback unavailable: {e}",
            )

        # Pick the HUD bedroom bracket closest to the requested model.
        hud_pick = _hud_closest(hud_estimates, body.bedrooms)
        if hud_pick is None or hud_pick.rent is None:
            detail = hud_note or rentcast_error or "No rent data available for this address."
            raise HTTPException(status_code=404, detail=detail)

        # Override the spec metadata to match the requested model so the
        # frontend renders consistent labels and per-SF math.
        hud_pick.spec_label = body.model_label or "Plinth Model"
        hud_pick.square_footage = body.square_footage
        if hud_pick.rent and body.square_footage:
            hud_pick.rent_psf_month = hud_pick.rent / body.square_footage
        est = hud_pick
        used_source = "hud_fmr"
        source_note = hud_note
    else:
        source_note = "Live RentCast comps for this property type and size."

    response = {
        "address": body.address,
        "applied_adu_premium": body.apply_adu_premium,
        "source": used_source,
        "source_note": source_note,
        "model_label": body.model_label,
        "estimate": estimate_to_dict(est),
    }

    _cache_put(db, key, response)
    return response


def _hud_closest(hud_estimates, target_beds: int):
    """Pick the HUD FMR bedroom bracket nearest to the requested bedroom count."""
    if not hud_estimates:
        return None
    spec_for_beds = {0: "studio", 1: "oneBr", 2: "twoBr"}
    target = spec_for_beds.get(target_beds, "twoBr")
    for e in hud_estimates:
        if e.spec_key == target and e.rent is not None:
            return e
    for e in hud_estimates:
        if e.rent is not None:
            return e
    return None
