import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from fastapi.middleware.cors import CORSMiddleware

from app.routes import export_boq, extract, quotations, structure, sourcing

app = FastAPI(
    title="BOQ Automation API",
    description="PDF/Excel table extraction for BOQ automation",
    version="0.1.0",
)

cors_origins = os.getenv(
    "CORS_ORIGINS", "http://localhost:3000"
).split(",")
cors_origins = [o.strip() for o in cors_origins if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(extract.router, prefix="/api")
app.include_router(structure.router, prefix="/api")
app.include_router(quotations.router, prefix="/api")
app.include_router(export_boq.router, prefix="/api")
app.include_router(sourcing.router, prefix="/api")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "BOQ Automation API",
        "status": "ok",
        "health": "/health",
        "docs": "/docs",
    }


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
