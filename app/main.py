import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from fastapi.middleware.cors import CORSMiddleware

from app.routes import export_boq, extract, quotations, structure, sourcing, rfq, takeoff, billing

app = FastAPI(
    title="BOQ Automation API",
    description="PDF/Excel table extraction for BOQ automation",
    version="0.1.0",
)

cors_origins = os.getenv(
    "CORS_ORIGINS", "http://localhost:3000"
).split(",")
cors_origins = [o.strip() for o in cors_origins if o.strip()]

# Add common local development origins to prevent port-mismatch CORS blocks
dev_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",
    "http://localhost:3003",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    "http://127.0.0.1:3002",
    "http://127.0.0.1:3003",
]
for o in dev_origins:
    if o not in cors_origins:
        cors_origins.append(o)

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
app.include_router(rfq.router, prefix="/api")
app.include_router(takeoff.router, prefix="/api")
app.include_router(billing.router, prefix="/api")


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
def health():
    import os
    from app.ai.structure import _get_gemini_api_key
    from app.ai.kimi import get_kimi_api_key
    
    gemini_key = _get_gemini_api_key()
    kimi_key = get_kimi_api_key()
    
    return {
        "status": "ok",
        "gemini_api_key_configured": bool(gemini_key),
        "gemini_api_key_length": len(gemini_key) if gemini_key else 0,
        "kimi_api_key_configured": bool(kimi_key),
        "kimi_api_key_length": len(kimi_key) if kimi_key else 0
    }
