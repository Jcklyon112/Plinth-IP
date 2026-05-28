"""
RentEstimateCache — short-TTL cache so duplicate-address submissions
don't burn RentCast/HUD quota.

Lookup key is the normalized address string. We store the serialized
response payload as JSON text and the source ('rentcast'|'hud_fmr'|'mixed')
for diagnostics.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database import Base


class RentEstimateCache(Base):
    __tablename__ = "rent_estimate_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    address_key = Column(String(512), unique=True, nullable=False, index=True)
    source = Column(String(32), nullable=False)
    payload_json = Column(Text, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)


def normalize_address_key(address: str) -> str:
    """Lowercase, collapse whitespace, drop trailing commas. Same address spelled differently still hits."""
    return " ".join(address.lower().split()).strip().rstrip(",")
