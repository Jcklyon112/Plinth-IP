"""drop UNIQUE constraint on grid_*.hifld_id

Revision ID: 004
Revises: 003
Create Date: 2026-05-07

The Phase 1 schema (003) declared `hifld_id` UNIQUE on substations,
transmission_lines, power_plants, and gas_pipelines on the assumption
that HIFLD's `ID` field is a stable per-asset identifier. The post-2025
HIFLD republish (services5/HDRa0B57OVrv2E1q) violates that assumption:
the substations layer alone has multiple records sharing IDs (observed:
306773 appears twice). The UNIQUE constraint blocks the loader from
committing a complete national snapshot.

This migration drops the four UNIQUE constraints and replaces each with
a non-unique B-tree index so analyst-side reverse lookups by hifld_id
remain fast. The `id` UUID PK on each table is unaffected.

The four affected tables:
  - grid_substations           (75,328 rows in May 2026 snapshot)
  - grid_transmission_lines    (89,744 rows)
  - grid_power_plants          (11,810 rows)
  - grid_gas_pipelines         (32,851 rows; FedMaps republish)

The fiber, balancing-authority, and service-territory tables don't carry
a hifld_id column and are not touched.
"""
from alembic import op


revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


# Auto-generated constraint names from migration 003 (sa.Column(unique=True)).
# Postgres uses <table>_<column>_key as the default name.
_TARGETS = [
    ("grid_substations",          "grid_substations_hifld_id_key",          "idx_grid_substations_hifld_id"),
    ("grid_transmission_lines",   "grid_transmission_lines_hifld_id_key",   "idx_grid_transmission_lines_hifld_id"),
    ("grid_power_plants",         "grid_power_plants_hifld_id_key",         "idx_grid_power_plants_hifld_id"),
    ("grid_gas_pipelines",        "grid_gas_pipelines_hifld_id_key",        "idx_grid_gas_pipelines_hifld_id"),
]


def upgrade():
    for table, constraint, index in _TARGETS:
        # IF EXISTS so the migration is idempotent if a constraint was
        # already dropped manually during incident response.
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "{constraint}"')
        op.create_index(index, table, ["hifld_id"], if_not_exists=True)


def downgrade():
    # Restoring the UNIQUE constraint will fail if duplicate hifld_id rows
    # exist (which is the entire reason this migration exists). The
    # downgrade is a best-effort: drop the index, then attempt to recreate
    # the constraint. Operators downgrading on a populated DB should
    # de-duplicate first.
    for table, constraint, index in _TARGETS:
        op.execute(f'DROP INDEX IF EXISTS {index}')
        op.execute(
            f'ALTER TABLE {table} '
            f'ADD CONSTRAINT "{constraint}" UNIQUE (hifld_id)'
        )
