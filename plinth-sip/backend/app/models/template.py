import uuid
from sqlalchemy import Column, String, Integer, Numeric, Boolean, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.database import Base


class PlinthTemplate(Base):
    __tablename__ = "plinth_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id = Column(String, unique=True, nullable=False)
    template_name = Column(String, nullable=False)
    footprint_width_ft = Column(Numeric, nullable=False)
    footprint_depth_ft = Column(Numeric, nullable=False)
    footprint_area_sqft = Column(Numeric, nullable=False)
    height_ft = Column(Numeric)
    bedrooms = Column(Integer)
    parking_assumption = Column(String)   # none | one_space | two_spaces
    delivery_assumption = Column(String)  # standard | truck | crane
    siting_type = Column(String)          # detached | attached | accessory
    active_status = Column(Boolean, default=True, nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
