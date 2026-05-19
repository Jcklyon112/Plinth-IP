"""
Seed script: loads Plinth templates and every municipality config in
$CONFIGS_DIR/municipalities/ into the database. Idempotent — re-run safely
to pick up new configs or updated template dimensions.
"""
import sys
import os
import json
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.municipality import Municipality, MunicipalityConfig
from app.models.template import PlinthTemplate

CONFIGS_DIR = os.environ.get("CONFIGS_DIR", "/configs")

TEMPLATES = [
    {
        "template_id": "plinth_studio",
        "template_name": "Plinth Studio",
        "footprint_width_ft": 15,
        "footprint_depth_ft": 35,
        "footprint_area_sqft": 525,
        "height_ft": 14,
        "bedrooms": 0,
        "parking_assumption": "none",
        "delivery_assumption": "truck",
        "siting_type": "detached",
        "active_status": True,
        "notes": "Standard Plinth footprint: 15x35 ft (525 sqft). Studio / flex space. No bedroom load on septic. Can be oriented in either direction.",
    },
    {
        "template_id": "plinth_1br",
        "template_name": "Plinth 1BR",
        "footprint_width_ft": 15,
        "footprint_depth_ft": 35,
        "footprint_area_sqft": 525,
        "height_ft": 16,
        "bedrooms": 1,
        "parking_assumption": "one_space",
        "delivery_assumption": "truck",
        "siting_type": "detached",
        "active_status": True,
        "notes": "Standard Plinth footprint: 15x35 ft (525 sqft). 1-bedroom. Requires 1 parking space and septic capacity for 1 bedroom. Can be oriented in either direction.",
    },
]

def _seed_municipality_from_config(db, config_path: str) -> None:
    """Upsert a single municipality + its config from a JSON file."""
    with open(config_path) as f:
        config_data = json.load(f)

    muni_id = config_data.get("municipality_id")
    if not muni_id:
        print(f"  ! Skipping {os.path.basename(config_path)} — no municipality_id")
        return

    muni_name = config_data.get("municipality_name") or muni_id
    state = config_data.get("state", "")
    county = config_data.get("county", "")

    m = db.query(Municipality).filter(Municipality.municipality_id == muni_id).first()
    if not m:
        m = Municipality(municipality_id=muni_id, name=muni_name, state=state, county=county)
        db.add(m)
        db.flush()
        print(f"  + Municipality: {muni_id} ({muni_name}, {state})")
    else:
        # Keep municipality row in sync with config metadata
        m.name = muni_name
        m.state = state
        m.county = county
        print(f"  ~ Municipality: {muni_id} ({muni_name}, {state})")

    version = int(config_data.get("config_version") or 1)
    existing_config = db.query(MunicipalityConfig).filter(
        MunicipalityConfig.municipality_id == muni_id,
        MunicipalityConfig.active == True,  # noqa: E712
    ).first()

    if not existing_config:
        cfg = MunicipalityConfig(
            municipality_id=muni_id,
            version=version,
            active=True,
            config_data=config_data,
            notes=f"Seeded from {os.path.basename(config_path)}",
        )
        db.add(cfg)
        print(f"    + Config v{version}")
    elif existing_config.version < version:
        # Newer config in file → activate as new version
        existing_config.active = False
        cfg = MunicipalityConfig(
            municipality_id=muni_id,
            version=version,
            active=True,
            config_data=config_data,
            notes=f"Re-seeded from {os.path.basename(config_path)}",
        )
        db.add(cfg)
        print(f"    + Config v{version} (replaced v{existing_config.version})")
    else:
        # Same version — refresh data in place to pick up edits during dev
        existing_config.config_data = config_data
        print(f"    ~ Config v{existing_config.version} refreshed")


def seed():
    db = SessionLocal()
    try:
        # Templates — upsert so dimension changes take effect
        for t_data in TEMPLATES:
            existing = db.query(PlinthTemplate).filter(
                PlinthTemplate.template_id == t_data["template_id"]
            ).first()
            if not existing:
                db.add(PlinthTemplate(**t_data))
                print(f"  + Template: {t_data['template_id']}")
            else:
                for k, v in t_data.items():
                    setattr(existing, k, v)
                print(f"  ~ Template updated: {t_data['template_id']}")

        # Municipalities — scan every JSON in CONFIGS_DIR/municipalities/
        muni_dir = os.path.join(CONFIGS_DIR, "municipalities")
        if not os.path.isdir(muni_dir):
            print(f"  ! Municipality config dir not found: {muni_dir}")
        else:
            paths = sorted(glob.glob(os.path.join(muni_dir, "*.json")))
            print(f"\nLoading {len(paths)} municipality configs from {muni_dir}")
            for path in paths:
                _seed_municipality_from_config(db, path)

        db.commit()
        print("\nSeed complete.")

    except Exception as e:
        db.rollback()
        print(f"Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("Seeding database...")
    seed()
