import uuid
from sqlalchemy import Column, String, Integer, Numeric, Text, DateTime, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from geoalchemy2 import Geometry
from app.database import Base


class Parcel(Base):
    __tablename__ = "parcels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcel_id = Column(String, nullable=False)
    municipality_id = Column(String, ForeignKey("municipalities.municipality_id"), nullable=False)
    address = Column(Text)
    owner_name = Column(Text)
    owner_mailing_address = Column(Text)
    zoning_code = Column(String)
    lot_area_sqft = Column(Numeric)
    land_use_type = Column(String)
    assessed_use = Column(String)
    existing_building_footprint_area = Column(Numeric)
    existing_structure_count = Column(Integer)
    raw_source_references = Column(JSONB)
    first_seen_scan_run_id = Column(UUID(as_uuid=True), ForeignKey("scan_runs.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("parcel_id", "municipality_id", name="uq_parcel_municipality"),
    )


class ParcelGeometry(Base):
    __tablename__ = "parcel_geometries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcel_id = Column(String, nullable=False)
    municipality_id = Column(String, nullable=False)
    geom = Column(Geometry("MULTIPOLYGON", srid=4326), nullable=False)
    area_sqft_calculated = Column(Numeric)
    scan_run_id = Column(UUID(as_uuid=True), ForeignKey("scan_runs.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ParcelRuleResult(Base):
    __tablename__ = "parcel_rule_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcel_id = Column(String, nullable=False)
    municipality_id = Column(String, nullable=False)
    scan_run_id = Column(UUID(as_uuid=True), ForeignKey("scan_runs.id"), nullable=False)
    rule_id = Column(String, nullable=False)
    rule_category = Column(String, nullable=False)
    result = Column(String, nullable=False)  # pass | conditional | fail | unknown
    explanation = Column(Text)
    assumptions_used = Column(JSONB)
    confidence = Column(Numeric)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ParcelScore(Base):
    __tablename__ = "parcel_scores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcel_id = Column(String, nullable=False)
    municipality_id = Column(String, nullable=False)
    scan_run_id = Column(UUID(as_uuid=True), ForeignKey("scan_runs.id"), nullable=False)
    scoring_profile = Column(String, default="default", nullable=False)
    score = Column(Numeric)
    tier = Column(Integer)
    score_breakdown = Column(JSONB, nullable=False)
    confidence = Column(Numeric)
    template_fits = Column(JSONB)
    blockers = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ParcelAnalystRecord(Base):
    __tablename__ = "parcel_analyst_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcel_id = Column(String, nullable=False)
    municipality_id = Column(String, nullable=False)
    analyst = Column(String)
    review_status = Column(String, default="unreviewed")
    outreach_status = Column(String, default="none")
    next_step = Column(Text)
    confidence_override = Column(Numeric)
    rule_overrides = Column(JSONB)
    notes = Column(Text)
    flagged = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
