import json
import os
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.municipality import Municipality, MunicipalityConfig
from app.config import settings

router = APIRouter()


@router.get("/")
def list_municipalities(db: Session = Depends(get_db)):
    municipalities = db.query(Municipality).filter(Municipality.active == True).all()
    return [
        {
            "municipality_id": m.municipality_id,
            "name": m.name,
            "county": m.county,
            "state": m.state,
        }
        for m in municipalities
    ]


@router.get("/{municipality_id}")
def get_municipality(municipality_id: str, db: Session = Depends(get_db)):
    m = db.query(Municipality).filter(
        Municipality.municipality_id == municipality_id
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail="Municipality not found")

    config = db.query(MunicipalityConfig).filter(
        MunicipalityConfig.municipality_id == municipality_id,
        MunicipalityConfig.active == True,
    ).first()

    return {
        "municipality_id": m.municipality_id,
        "name": m.name,
        "county": m.county,
        "state": m.state,
        "config_version": config.version if config else None,
        "config": config.config_data if config else None,
    }


@router.post("/")
def create_municipality(data: dict = Body(...), db: Session = Depends(get_db)):
    existing = db.query(Municipality).filter(
        Municipality.municipality_id == data["municipality_id"]
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Municipality already exists")

    m = Municipality(
        municipality_id=data["municipality_id"],
        name=data["name"],
        county=data.get("county"),
        state=data["state"],
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"municipality_id": m.municipality_id, "status": "created"}


@router.post("/load-from-file/{municipality_id}")
def load_municipality_from_config_file(municipality_id: str, db: Session = Depends(get_db)):
    """Load or update a municipality and its config from the configs directory."""
    config_path = os.path.join(settings.CONFIGS_DIR, "municipalities", f"{municipality_id}.json")
    if not os.path.exists(config_path):
        raise HTTPException(status_code=404, detail=f"Config file not found: {config_path}")

    with open(config_path) as f:
        config_data = json.load(f)

    # Upsert municipality
    m = db.query(Municipality).filter(Municipality.municipality_id == municipality_id).first()
    if not m:
        m = Municipality(
            municipality_id=municipality_id,
            name=config_data["municipality_name"],
            county=config_data.get("county"),
            state=config_data["state"],
        )
        db.add(m)
        db.flush()

    # Deactivate existing configs
    db.query(MunicipalityConfig).filter(
        MunicipalityConfig.municipality_id == municipality_id
    ).update({"active": False})

    # Determine next version
    latest = db.query(MunicipalityConfig).filter(
        MunicipalityConfig.municipality_id == municipality_id
    ).order_by(MunicipalityConfig.version.desc()).first()
    next_version = (latest.version + 1) if latest else 1

    config = MunicipalityConfig(
        municipality_id=municipality_id,
        version=next_version,
        active=True,
        config_data=config_data,
        notes=f"Loaded from file {municipality_id}.json",
    )
    db.add(config)
    db.commit()

    return {
        "municipality_id": municipality_id,
        "config_version": next_version,
        "status": "loaded",
    }
