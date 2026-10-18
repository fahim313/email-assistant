from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import SessionLocal

settings = get_settings()

app = FastAPI(
    title="Email Assistant API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "email-assistant-api",
    }


@app.get("/api/health/db", tags=["Health"])
async def database_health_check() -> dict[str, str]:
    async with SessionLocal() as session:
        _ = await session.execute(text("SELECT 1"))

    return {
        "status": "healthy",
        "database": "connected",
    }