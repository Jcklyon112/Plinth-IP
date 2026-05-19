"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
import geoalchemy2

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.create_table(
        "municipalities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("municipality_id", sa.String, unique=True, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("county", sa.String),
        sa.Column("state", sa.String, nullable=False),
        sa.Column("active", sa.Boolean, default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "municipality_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("municipality_id", sa.String, sa.ForeignKey("municipalities.municipality_id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("active", sa.Boolean, default=False, nullable=False),
        sa.Column("config_data", JSONB, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_by", sa.String),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "municipality_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("municipality_id", sa.String, sa.ForeignKey("municipalities.municipality_id"), nullable=False),
        sa.Column("source_type", sa.String, nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("source_label", sa.String),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("format", sa.String),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "scan_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("municipality_id", sa.String, sa.ForeignKey("municipalities.municipality_id"), nullable=False),
        sa.Column("config_version", sa.Integer, nullable=False),
        sa.Column("status", sa.String, default="pending", nullable=False),
        sa.Column("parcels_ingested", sa.Integer),
        sa.Column("parcels_scored", sa.Integer),
        sa.Column("run_type", sa.String, default="full", nullable=False),
        sa.Column("triggered_by", sa.String),
        sa.Column("error_log", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "plinth_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("template_id", sa.String, unique=True, nullable=False),
        sa.Column("template_name", sa.String, nullable=False),
        sa.Column("footprint_width_ft", sa.Numeric, nullable=False),
        sa.Column("footprint_depth_ft", sa.Numeric, nullable=False),
        sa.Column("footprint_area_sqft", sa.Numeric, nullable=False),
        sa.Column("height_ft", sa.Numeric),
        sa.Column("bedrooms", sa.Integer),
        sa.Column("parking_assumption", sa.String),
        sa.Column("delivery_assumption", sa.String),
        sa.Column("siting_type", sa.String),
        sa.Column("active_status", sa.Boolean, default=True, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "parcels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcel_id", sa.String, nullable=False),
        sa.Column("municipality_id", sa.String, sa.ForeignKey("municipalities.municipality_id"), nullable=False),
        sa.Column("address", sa.Text),
        sa.Column("owner_name", sa.Text),
        sa.Column("owner_mailing_address", sa.Text),
        sa.Column("zoning_code", sa.String),
        sa.Column("lot_area_sqft", sa.Numeric),
        sa.Column("land_use_type", sa.String),
        sa.Column("assessed_use", sa.String),
        sa.Column("existing_building_footprint_area", sa.Numeric),
        sa.Column("existing_structure_count", sa.Integer),
        sa.Column("raw_source_references", JSONB),
        sa.Column("first_seen_scan_run_id", UUID(as_uuid=True), sa.ForeignKey("scan_runs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("parcel_id", "municipality_id", name="uq_parcel_municipality"),
    )

    op.create_table(
        "parcel_geometries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcel_id", sa.String, nullable=False),
        sa.Column("municipality_id", sa.String, nullable=False),
        sa.Column("geom", geoalchemy2.types.Geometry("MULTIPOLYGON", srid=4326), nullable=False),
        sa.Column("area_sqft_calculated", sa.Numeric),
        sa.Column("scan_run_id", UUID(as_uuid=True), sa.ForeignKey("scan_runs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_parcel_geometries_geom", "parcel_geometries", ["geom"], postgresql_using="gist", if_not_exists=True)

    op.create_table(
        "parcel_rule_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcel_id", sa.String, nullable=False),
        sa.Column("municipality_id", sa.String, nullable=False),
        sa.Column("scan_run_id", UUID(as_uuid=True), sa.ForeignKey("scan_runs.id"), nullable=False),
        sa.Column("rule_id", sa.String, nullable=False),
        sa.Column("rule_category", sa.String, nullable=False),
        sa.Column("result", sa.String, nullable=False),
        sa.Column("explanation", sa.Text),
        sa.Column("assumptions_used", JSONB),
        sa.Column("confidence", sa.Numeric),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "parcel_scores",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcel_id", sa.String, nullable=False),
        sa.Column("municipality_id", sa.String, nullable=False),
        sa.Column("scan_run_id", UUID(as_uuid=True), sa.ForeignKey("scan_runs.id"), nullable=False),
        sa.Column("scoring_profile", sa.String, default="default", nullable=False),
        sa.Column("score", sa.Numeric),
        sa.Column("tier", sa.Integer),
        sa.Column("score_breakdown", JSONB, nullable=False),
        sa.Column("confidence", sa.Numeric),
        sa.Column("template_fits", JSONB),
        sa.Column("blockers", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "parcel_analyst_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcel_id", sa.String, nullable=False),
        sa.Column("municipality_id", sa.String, nullable=False),
        sa.Column("analyst", sa.String),
        sa.Column("review_status", sa.String, default="unreviewed"),
        sa.Column("outreach_status", sa.String, default="none"),
        sa.Column("next_step", sa.Text),
        sa.Column("confidence_override", sa.Numeric),
        sa.Column("rule_overrides", JSONB),
        sa.Column("notes", sa.Text),
        sa.Column("flagged", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "overlays",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("municipality_id", sa.String, sa.ForeignKey("municipalities.municipality_id"), nullable=True),
        sa.Column("overlay_type", sa.String, nullable=False),
        sa.Column("label", sa.String, nullable=False),
        sa.Column("geom", geoalchemy2.types.Geometry("MULTIPOLYGON", srid=4326)),
        sa.Column("source_url", sa.Text),
        sa.Column("active", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_overlays_geom", "overlays", ["geom"], postgresql_using="gist", if_not_exists=True)

    op.create_table(
        "exports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("municipality_id", sa.String, sa.ForeignKey("municipalities.municipality_id")),
        sa.Column("scan_run_id", UUID(as_uuid=True), sa.ForeignKey("scan_runs.id")),
        sa.Column("export_type", sa.String, nullable=False),
        sa.Column("filter_params", JSONB),
        sa.Column("parcel_count", sa.Integer),
        sa.Column("file_path", sa.Text),
        sa.Column("created_by", sa.String),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("exports")
    op.drop_index("idx_overlays_geom", table_name="overlays")
    op.drop_table("overlays")
    op.drop_table("parcel_analyst_records")
    op.drop_table("parcel_scores")
    op.drop_table("parcel_rule_results")
    op.drop_index("idx_parcel_geometries_geom", table_name="parcel_geometries")
    op.drop_table("parcel_geometries")
    op.drop_table("parcels")
    op.drop_table("plinth_templates")
    op.drop_table("scan_runs")
    op.drop_table("municipality_sources")
    op.drop_table("municipality_configs")
    op.drop_table("municipalities")
