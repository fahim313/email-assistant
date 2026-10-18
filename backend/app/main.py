from fastapi import FastAPI
from sqlalchemy import text

from app.db.session import SessionLocal

app = FastAPI(
    title="Email Assistant API",
    version="1.0.0",
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