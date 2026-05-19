from app.ingestion.adapters.massgis import MASSGIS_ADAPTER, massgis_field_transform

ADAPTER_REGISTRY = {
    "massgis": {
        "adapter": MASSGIS_ADAPTER,
        "transform": massgis_field_transform,
    }
}
