from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.routers import rent_estimate
# Importing models registers them with Base.metadata so create_all picks them up.
from app import models  # noqa: F401

app = FastAPI(
    title="Plinth ADU Rent Calculator API",
    version="0.1.0",
    description="Address-in, ADU rent estimate out. RentCast-backed with HUD FMR fallback.",
)

Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rent_estimate.router, tags=["Rent Estimate"])


@app.get("/health")
def health():
    return {"status": "ok", "service": "plinth-adu-rent-calculator-api"}
