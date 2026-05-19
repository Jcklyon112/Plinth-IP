from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.template import PlinthTemplate

router = APIRouter()


@router.get("/")
def list_templates(db: Session = Depends(get_db)):
    templates = db.query(PlinthTemplate).filter(PlinthTemplate.active_status == True).all()
    return [_template_to_dict(t) for t in templates]


@router.post("/")
def create_template(data: dict = Body(...), db: Session = Depends(get_db)):
    existing = db.query(PlinthTemplate).filter(
        PlinthTemplate.template_id == data["template_id"]
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Template ID already exists")

    t = PlinthTemplate(
        template_id=data["template_id"],
        template_name=data["template_name"],
        footprint_width_ft=data["footprint_width_ft"],
        footprint_depth_ft=data["footprint_depth_ft"],
        footprint_area_sqft=data["footprint_area_sqft"],
        height_ft=data.get("height_ft"),
        bedrooms=data.get("bedrooms"),
        parking_assumption=data.get("parking_assumption", "none"),
        delivery_assumption=data.get("delivery_assumption", "standard"),
        siting_type=data.get("siting_type", "detached"),
        active_status=data.get("active_status", True),
        notes=data.get("notes"),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _template_to_dict(t)


@router.put("/{template_id}")
def update_template(template_id: str, data: dict = Body(...), db: Session = Depends(get_db)):
    t = db.query(PlinthTemplate).filter(PlinthTemplate.template_id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    for field in ["template_name", "footprint_width_ft", "footprint_depth_ft",
                  "footprint_area_sqft", "height_ft", "bedrooms", "parking_assumption",
                  "delivery_assumption", "siting_type", "active_status", "notes"]:
        if field in data:
            setattr(t, field, data[field])

    db.commit()
    return _template_to_dict(t)


def _template_to_dict(t: PlinthTemplate) -> dict:
    return {
        "template_id": t.template_id,
        "template_name": t.template_name,
        "footprint_width_ft": float(t.footprint_width_ft),
        "footprint_depth_ft": float(t.footprint_depth_ft),
        "footprint_area_sqft": float(t.footprint_area_sqft),
        "height_ft": float(t.height_ft) if t.height_ft else None,
        "bedrooms": t.bedrooms,
        "parking_assumption": t.parking_assumption,
        "delivery_assumption": t.delivery_assumption,
        "siting_type": t.siting_type,
        "active_status": t.active_status,
        "notes": t.notes,
    }
