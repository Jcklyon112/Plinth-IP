"""Ensure PostGIS spatial indexes exist for parcel geometry queries."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import engine
from sqlalchemy import text

def main():
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_parcel_geom_gist "
            "ON parcel_geometries USING GIST (geom);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_parcels_muni "
            "ON parcels (municipality_id);"
        ))
        conn.commit()
        print("Indexes verified/created.")

if __name__ == "__main__":
    main()
