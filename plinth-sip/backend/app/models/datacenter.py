"""SQLAlchemy models for the data-center feasibility analyzer.

Mirrors alembic revision 003. None of these models touch the ADU pipeline;
they are loaded by `data/grid/` ingestion scripts and consumed by
`app/engine/datacenter/`.
"""
import uuid
from sqlalchemy import (
    Column,
    String,
    Integer,
    Numeric,
    Text,
    DateTime,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from geoalchemy2 import Geometry
from app.database import Base


# --- HIFLD layers ---------------------------------------------------------

class GridSubstation(Base):
    __tablename__ = "grid_substations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hifld_id = Column(String, unique=True)
    name = Column(Text)
    operator = Column(Text)
    type = Column(String)
    status = Column(String)
    max_voltage_kv = Column(Integer)
    min_voltage_kv = Column(Integer)
    lines_count = Column(Integer)
    geom = Column(Geometry("POINT", srid=4326), nullable=False)
    source_refresh_at = Column(DateTime(timezone=True))
    attributes = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GridTransmissionLine(Base):
    __tablename__ = "grid_transmission_lines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hifld_id = Column(String, unique=True)
    owner = Column(Text)
    voltage_kv = Column(Integer)
    voltage_class = Column(String)
    type = Column(String)
    status = Column(String)
    geom = Column(Geometry("MULTILINESTRING", srid=4326), nullable=False)
    source_refresh_at = Column(DateTime(timezone=True))
    attributes = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GridPowerPlant(Base):
    __tablename__ = "grid_power_plants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hifld_id = Column(String, unique=True)
    name = Column(Text)
    operator = Column(Text)
    primary_fuel = Column(String)  # nuclear|gas|coal|wind|solar|hydro|oil|biomass|geothermal|battery|other
    total_mw = Column(Numeric)
    summer_capacity_mw = Column(Numeric)
    status = Column(String)
    geom = Column(Geometry("POINT", srid=4326), nullable=False)
    source_refresh_at = Column(DateTime(timezone=True))
    attributes = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GridBalancingAuthority(Base):
    __tablename__ = "grid_balancing_authorities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ba_code = Column(String)
    ba_name = Column(Text)
    iso_rto = Column(String)  # PJM|MISO|ERCOT|CAISO|NYISO|ISO-NE|SPP|NON-ISO
    geom = Column(Geometry("MULTIPOLYGON", srid=4326), nullable=False)
    source_refresh_at = Column(DateTime(timezone=True))
    attributes = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GridServiceTerritory(Base):
    __tablename__ = "grid_service_territories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    utility_id_eia = Column(Integer)
    utility_name = Column(Text)
    holding_company = Column(Text)
    state = Column(String)
    geom = Column(Geometry("MULTIPOLYGON", srid=4326), nullable=False)
    source_refresh_at = Column(DateTime(timezone=True))
    attributes = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GridGasPipeline(Base):
    __tablename__ = "grid_gas_pipelines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hifld_id = Column(String, unique=True)
    operator = Column(Text)
    type = Column(String)
    status = Column(String)
    diameter_in = Column(Numeric)
    geom = Column(Geometry("MULTILINESTRING", srid=4326), nullable=False)
    source_refresh_at = Column(DateTime(timezone=True))
    attributes = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GridFiberRoute(Base):
    __tablename__ = "grid_fiber_routes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_label = Column(String, nullable=False)
    carrier = Column(String)
    geom = Column(Geometry("MULTILINESTRING", srid=4326), nullable=False)
    source_refresh_at = Column(DateTime(timezone=True))
    attributes = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# --- EIA rates -----------------------------------------------------------

class EiaIndustrialRate(Base):
    __tablename__ = "eia_industrial_rates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    utility_id_eia = Column(Integer, nullable=False)
    utility_name = Column(Text)
    year = Column(Integer, nullable=False)
    sector = Column(String, nullable=False, default="industrial")
    rate_cents_per_kwh = Column(Numeric)
    revenue_thousand_usd = Column(Numeric)
    sales_mwh = Column(Numeric)
    customers = Column(Integer)
    source_url = Column(Text)
    source_refresh_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("utility_id_eia", "year", "sector", name="uq_eia_rate_utility_year_sector"),
    )


# --- bookkeeping ---------------------------------------------------------

class GridRefreshMetadata(Base):
    """One row per loader. Updated whenever a loader successfully refreshes
    its layer. The analyzer reads `last_refresh_at` across the layers it
    consults to compute a cache-invalidation key.
    """
    __tablename__ = "grid_refresh_metadata"

    layer_name = Column(String, primary_key=True)
    last_refresh_at = Column(DateTime(timezone=True))
    feature_count = Column(Integer)
    source_url = Column(Text)
    source_label = Column(String)
    notes = Column(Text)


class ParcelDataCenterAnalysis(Base):
    """On-demand analyzer result cache.

    `grid_data_version` is a deterministic hash of refresh timestamps for
    the input layers; refreshing any of them invalidates prior rows for
    the same parcel.
    """
    __tablename__ = "parcel_datacenter_analyses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parcel_id = Column(String, nullable=False)
    municipality_id = Column(String, nullable=False)
    grid_data_version = Column(String, nullable=False)
    result = Column(JSONB, nullable=False)
    computed_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "parcel_id",
            "municipality_id",
            "grid_data_version",
            name="uq_parcel_dc_analysis_version",
        ),
    )
