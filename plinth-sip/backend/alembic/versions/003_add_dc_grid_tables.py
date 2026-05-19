"""add data center feasibility grid tables

Revision ID: 003
Revises: 002
Create Date: 2026-05-07

Adds the data layer for the data-center feasibility analyzer:

  - grid_substations              (HIFLD electric substations, point)
  - grid_transmission_lines       (HIFLD transmission lines, multilinestring)
  - grid_power_plants             (HIFLD/EIA Form 860 plants, point)
  - grid_balancing_authorities    (HIFLD BAs -> ISO/RTO derivation, multipolygon)
  - grid_service_territories      (HIFLD electric retail service territories, multipolygon)
  - grid_gas_pipelines            (HIFLD natural gas pipelines, multilinestring)
  - grid_fiber_routes             (HIFLD long-haul fiber + carrier KMZ drop-ins, multilinestring)
  - eia_industrial_rates          (EIA Form 861 industrial retail rates by utility)
  - grid_refresh_metadata         (one row per loader, tracks last refresh)
  - parcel_datacenter_analyses    (on-demand analyzer result cache)

All geometry columns are EPSG:4326 with GIST indexes. None of these tables
reference the ADU pipeline; the ADU rules engine is unchanged.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
import geoalchemy2

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


# --- helpers --------------------------------------------------------------

def _gist(name: str, table: str, column: str = "geom") -> None:
    op.create_index(name, table, [column], postgresql_using="gist", if_not_exists=True)


# --- upgrade --------------------------------------------------------------

def upgrade():
    # ---------------------------------------------------------------------
    # Electric substations (HIFLD)
    # Point geometry. We retain MAX_VOLT / MIN_VOLT separately because the
    # >=115kV cutoff used by the proximity rule is voltage-dependent.
    # ---------------------------------------------------------------------
    op.create_table(
        "grid_substations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("hifld_id", sa.String, unique=True),
        sa.Column("name", sa.Text),
        sa.Column("operator", sa.Text),
        sa.Column("type", sa.String),
        sa.Column("status", sa.String),
        sa.Column("max_voltage_kv", sa.Integer),
        sa.Column("min_voltage_kv", sa.Integer),
        sa.Column("lines_count", sa.Integer),
        sa.Column("geom", geoalchemy2.types.Geometry("POINT", srid=4326), nullable=False),
        sa.Column("source_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("attributes", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    _gist("idx_grid_substations_geom", "grid_substations")
    op.create_index(
        "idx_grid_substations_voltage",
        "grid_substations",
        ["max_voltage_kv"],
        if_not_exists=True,
    )

    # ---------------------------------------------------------------------
    # Transmission lines (HIFLD)
    # MULTILINESTRING because HIFLD occasionally exports merged segments as
    # MultiLineString; storing as MULTI lets us COPY without normalization.
    # ---------------------------------------------------------------------
    op.create_table(
        "grid_transmission_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("hifld_id", sa.String, unique=True),
        sa.Column("owner", sa.Text),
        sa.Column("voltage_kv", sa.Integer),
        sa.Column("voltage_class", sa.String),  # e.g. "UNDER 100", "100-161", "220-287", "345", "500", "735 AND ABOVE"
        sa.Column("type", sa.String),
        sa.Column("status", sa.String),
        sa.Column("geom", geoalchemy2.types.Geometry("MULTILINESTRING", srid=4326), nullable=False),
        sa.Column("source_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("attributes", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    _gist("idx_grid_transmission_lines_geom", "grid_transmission_lines")
    op.create_index(
        "idx_grid_transmission_lines_voltage",
        "grid_transmission_lines",
        ["voltage_kv"],
        if_not_exists=True,
    )

    # ---------------------------------------------------------------------
    # Power plants (HIFLD / EIA Form 860)
    # primary_fuel is normalized to: nuclear|gas|coal|wind|solar|hydro|oil|
    # biomass|geothermal|battery|other. summer_capacity_mw is preferred over
    # total_mw for "available capacity" computations.
    # ---------------------------------------------------------------------
    op.create_table(
        "grid_power_plants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("hifld_id", sa.String, unique=True),
        sa.Column("name", sa.Text),
        sa.Column("operator", sa.Text),
        sa.Column("primary_fuel", sa.String),
        sa.Column("total_mw", sa.Numeric),
        sa.Column("summer_capacity_mw", sa.Numeric),
        sa.Column("status", sa.String),
        sa.Column("geom", geoalchemy2.types.Geometry("POINT", srid=4326), nullable=False),
        sa.Column("source_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("attributes", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    _gist("idx_grid_power_plants_geom", "grid_power_plants")
    op.create_index(
        "idx_grid_power_plants_fuel",
        "grid_power_plants",
        ["primary_fuel"],
        if_not_exists=True,
    )

    # ---------------------------------------------------------------------
    # Balancing authority polygons -> ISO/RTO derivation
    # iso_rto: PJM | MISO | ERCOT | CAISO | NYISO | ISO-NE | SPP | NON-ISO
    # Source: HIFLD "Control Areas" / Balancing Authority Areas. The mapping
    # from BA code to ISO is maintained in data/grid/iso_metadata.json.
    # ---------------------------------------------------------------------
    op.create_table(
        "grid_balancing_authorities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ba_code", sa.String),
        sa.Column("ba_name", sa.Text),
        sa.Column("iso_rto", sa.String),
        sa.Column("geom", geoalchemy2.types.Geometry("MULTIPOLYGON", srid=4326), nullable=False),
        sa.Column("source_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("attributes", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    _gist("idx_grid_balancing_authorities_geom", "grid_balancing_authorities")
    op.create_index(
        "idx_grid_balancing_authorities_iso",
        "grid_balancing_authorities",
        ["iso_rto"],
        if_not_exists=True,
    )

    # ---------------------------------------------------------------------
    # Electric retail service territories (HIFLD)
    # Used to look up the utility serving a given parcel, then joined to
    # eia_industrial_rates by utility_id_eia for the rate tier.
    # ---------------------------------------------------------------------
    op.create_table(
        "grid_service_territories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("utility_id_eia", sa.Integer),
        sa.Column("utility_name", sa.Text),
        sa.Column("holding_company", sa.Text),
        sa.Column("state", sa.String),
        sa.Column("geom", geoalchemy2.types.Geometry("MULTIPOLYGON", srid=4326), nullable=False),
        sa.Column("source_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("attributes", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    _gist("idx_grid_service_territories_geom", "grid_service_territories")
    op.create_index(
        "idx_grid_service_territories_utility",
        "grid_service_territories",
        ["utility_id_eia"],
        if_not_exists=True,
    )

    # ---------------------------------------------------------------------
    # Natural gas pipelines (HIFLD interstate + intrastate)
    # ---------------------------------------------------------------------
    op.create_table(
        "grid_gas_pipelines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("hifld_id", sa.String, unique=True),
        sa.Column("operator", sa.Text),
        sa.Column("type", sa.String),
        sa.Column("status", sa.String),
        sa.Column("diameter_in", sa.Numeric),
        sa.Column("geom", geoalchemy2.types.Geometry("MULTILINESTRING", srid=4326), nullable=False),
        sa.Column("source_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("attributes", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    _gist("idx_grid_gas_pipelines_geom", "grid_gas_pipelines")

    # ---------------------------------------------------------------------
    # Long-haul fiber routes
    # source_label distinguishes data origin for reporting: "HIFLD",
    # "Lumen", "Zayo", "Crown Castle", or e.g. "Proxy:Interstate" if a
    # future fallback is ever enabled. Mixing is safe; analyzer just cares
    # about the nearest geometry.
    # ---------------------------------------------------------------------
    op.create_table(
        "grid_fiber_routes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_label", sa.String, nullable=False),
        sa.Column("carrier", sa.String),
        sa.Column("geom", geoalchemy2.types.Geometry("MULTILINESTRING", srid=4326), nullable=False),
        sa.Column("source_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("attributes", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    _gist("idx_grid_fiber_routes_geom", "grid_fiber_routes")
    op.create_index(
        "idx_grid_fiber_routes_source",
        "grid_fiber_routes",
        ["source_label"],
        if_not_exists=True,
    )

    # ---------------------------------------------------------------------
    # EIA Form 861 industrial retail rates by utility / year
    # ---------------------------------------------------------------------
    op.create_table(
        "eia_industrial_rates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("utility_id_eia", sa.Integer, nullable=False),
        sa.Column("utility_name", sa.Text),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("sector", sa.String, nullable=False, server_default="industrial"),
        sa.Column("rate_cents_per_kwh", sa.Numeric),
        sa.Column("revenue_thousand_usd", sa.Numeric),
        sa.Column("sales_mwh", sa.Numeric),
        sa.Column("customers", sa.Integer),
        sa.Column("source_url", sa.Text),
        sa.Column("source_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("utility_id_eia", "year", "sector", name="uq_eia_rate_utility_year_sector"),
    )
    op.create_index(
        "idx_eia_industrial_rates_utility",
        "eia_industrial_rates",
        ["utility_id_eia", "year"],
        if_not_exists=True,
    )

    # ---------------------------------------------------------------------
    # Per-layer refresh log. One row per loader; updated on each successful
    # refresh. Used to compute the cache invalidation key for the analyzer.
    # ---------------------------------------------------------------------
    op.create_table(
        "grid_refresh_metadata",
        sa.Column("layer_name", sa.String, primary_key=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("feature_count", sa.Integer),
        sa.Column("source_url", sa.Text),
        sa.Column("source_label", sa.String),
        sa.Column("notes", sa.Text),
    )

    # ---------------------------------------------------------------------
    # Result cache for the on-demand analyzer.
    # grid_data_version is computed by the analyzer as a deterministic hash
    # of grid_refresh_metadata.last_refresh_at across the layers actually
    # consulted. A refresh of any input layer invalidates prior cache rows.
    # ---------------------------------------------------------------------
    op.create_table(
        "parcel_datacenter_analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcel_id", sa.String, nullable=False),
        sa.Column("municipality_id", sa.String, nullable=False),
        sa.Column("grid_data_version", sa.String, nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "parcel_id",
            "municipality_id",
            "grid_data_version",
            name="uq_parcel_dc_analysis_version",
        ),
    )
    op.create_index(
        "idx_parcel_dc_analyses_parcel_muni",
        "parcel_datacenter_analyses",
        ["parcel_id", "municipality_id"],
        if_not_exists=True,
    )


# --- downgrade ------------------------------------------------------------

def downgrade():
    op.drop_index("idx_parcel_dc_analyses_parcel_muni", table_name="parcel_datacenter_analyses")
    op.drop_table("parcel_datacenter_analyses")

    op.drop_table("grid_refresh_metadata")

    op.drop_index("idx_eia_industrial_rates_utility", table_name="eia_industrial_rates")
    op.drop_table("eia_industrial_rates")

    op.drop_index("idx_grid_fiber_routes_source", table_name="grid_fiber_routes")
    op.drop_index("idx_grid_fiber_routes_geom", table_name="grid_fiber_routes")
    op.drop_table("grid_fiber_routes")

    op.drop_index("idx_grid_gas_pipelines_geom", table_name="grid_gas_pipelines")
    op.drop_table("grid_gas_pipelines")

    op.drop_index("idx_grid_service_territories_utility", table_name="grid_service_territories")
    op.drop_index("idx_grid_service_territories_geom", table_name="grid_service_territories")
    op.drop_table("grid_service_territories")

    op.drop_index("idx_grid_balancing_authorities_iso", table_name="grid_balancing_authorities")
    op.drop_index("idx_grid_balancing_authorities_geom", table_name="grid_balancing_authorities")
    op.drop_table("grid_balancing_authorities")

    op.drop_index("idx_grid_power_plants_fuel", table_name="grid_power_plants")
    op.drop_index("idx_grid_power_plants_geom", table_name="grid_power_plants")
    op.drop_table("grid_power_plants")

    op.drop_index("idx_grid_transmission_lines_voltage", table_name="grid_transmission_lines")
    op.drop_index("idx_grid_transmission_lines_geom", table_name="grid_transmission_lines")
    op.drop_table("grid_transmission_lines")

    op.drop_index("idx_grid_substations_voltage", table_name="grid_substations")
    op.drop_index("idx_grid_substations_geom", table_name="grid_substations")
    op.drop_table("grid_substations")
