from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.export import Export

router = APIRouter()


@router.get("/")
def list_exports(db: Session = Depends(get_db)):
    exports = db.query(Export).order_by(Export.created_at.desc()).limit(50).all()
    return [
        {
            "id": str(e.id),
            "municipality_id": e.municipality_id,
            "export_type": e.export_type,
            "parcel_count": e.parcel_count,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in exports
    ]
