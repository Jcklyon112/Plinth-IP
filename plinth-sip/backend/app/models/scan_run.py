import uuid
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.database import Base


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    municipality_id = Column(String, ForeignKey("municipalities.municipality_id"), nullable=False)
    config_version = Column(Integer, nullable=False)
    status = Column(String, default="pending", nullable=False)
    parcels_ingested = Column(Integer)
    parcels_scored = Column(Integer)
    run_type = Column(String, default="full", nullable=False)
    triggered_by = Column(String)
    error_log = Column(Text)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
