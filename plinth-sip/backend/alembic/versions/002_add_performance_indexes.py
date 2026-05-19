"""add performance indexes for parcels, parcel_scores, parcel_geometries

Revision ID: 002
Revises: 001
Create Date: 2026-04-04
"""
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    # parcels: lookups by municipality_id, zoning_code, land_use_type
    op.create_index(
        "idx_parcels_municipality_id",
        "parcels",
        ["municipality_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_parcels_zoning_code",
        "parcels",
        ["municipality_id", "zoning_code"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_parcels_land_use_type",
        "parcels",
        ["municipality_id", "land_use_type"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_parcels_lot_area",
        "parcels",
        ["municipality_id", "lot_area_sqft"],
        if_not_exists=True,
    )

    # parcel_scores: tier lookups, score sorting
    op.create_index(
        "idx_parcel_scores_municipality_tier",
        "parcel_scores",
        ["municipality_id", "tier"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_parcel_scores_scan_run",
        "parcel_scores",
        ["scan_run_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_parcel_scores_parcel_muni",
        "parcel_scores",
        ["parcel_id", "municipality_id"],
        if_not_exists=True,
    )

    # parcel_geometries: parcel lookups (geom GIST index already exists from 001)
    op.create_index(
        "idx_parcel_geometries_parcel_muni",
        "parcel_geometries",
        ["parcel_id", "municipality_id"],
        if_not_exists=True,
    )

    # parcel_rule_results: lookups by parcel + scan
    op.create_index(
        "idx_parcel_rule_results_parcel_muni",
        "parcel_rule_results",
        ["parcel_id", "municipality_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_parcel_rule_results_scan_run",
        "parcel_rule_results",
        ["scan_run_id"],
        if_not_exists=True,
    )

    # scan_runs: status + municipality
    op.create_index(
        "idx_scan_runs_municipality",
        "scan_runs",
        ["municipality_id", "status"],
        if_not_exists=True,
    )


def downgrade():
    op.drop_index("idx_scan_runs_municipality", table_name="scan_runs")
    op.drop_index("idx_parcel_rule_results_scan_run", table_name="parcel_rule_results")
    op.drop_index("idx_parcel_rule_results_parcel_muni", table_name="parcel_rule_results")
    op.drop_index("idx_parcel_geometries_parcel_muni", table_name="parcel_geometries")
    op.drop_index("idx_parcel_scores_parcel_muni", table_name="parcel_scores")
    op.drop_index("idx_parcel_scores_scan_run", table_name="parcel_scores")
    op.drop_index("idx_parcel_scores_municipality_tier", table_name="parcel_scores")
    op.drop_index("idx_parcels_lot_area", table_name="parcels")
    op.drop_index("idx_parcels_land_use_type", table_name="parcels")
    op.drop_index("idx_parcels_zoning_code", table_name="parcels")
    op.drop_index("idx_parcels_municipality_id", table_name="parcels")
