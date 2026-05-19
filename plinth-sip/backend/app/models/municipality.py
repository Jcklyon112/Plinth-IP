import uuid
from sqlalchemy import Column, String, Boolean, Integer, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.database import Base


class Municipality(Base):
    __tablename__ = "municipalities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    municipality_id = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    county = Column(String)
    state = Column(String, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MunicipalityConfig(Base):
    __tablename__ = "municipality_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    municipality_id = Column(String, ForeignKey("municipalities.municipality_id"), nullable=False)
    version = Column(Integer, nullable=False)
    active = Column(Boolean, default=False, nullable=False)
    config_data = Column(JSONB, nullable=False)
    notes = Column(Text)
    created_by = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MunicipalitySource(Base):
    __tablename__ = "municipality_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    municipality_id = Column(String, ForeignKey("municipalities.municipality_id"), nullable=False)
    source_type = Column(String, nullable=False)
    source_url = Column(Text)
    source_label = Column(String)
    last_fetched_at = Column(DateTime(timezone=True))
    format = Column(String)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
