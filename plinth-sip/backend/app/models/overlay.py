import uuid
from sqlalchemy import Column, String, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from geoalchemy2 import Geometry
from app.database import Base


class Overlay(Base):
    __tablename__ = "overlays"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    municipality_id = Column(String, ForeignKey("municipalities.municipality_id"), nullable=True)
    overlay_type = Column(String, nullable=False)
    label = Column(String, nullable=False)
    geom = Column(Geometry("MULTIPOLYGON", srid=4326))
    source_url = Column(Text)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
