from fastapi import APIRouter
from sqlalchemy import text

from app.db.session import SessionLocal
from app.modules.accounts.router import router as accounts_router
from app.modules.analysis.router import router as analysis_router
from app.modules.auth.router import router as auth_router
from app.modules.emails.router import router as emails_router
from app.modules.sync.router import router as sync_router
from app.modules.threads.router import router as threads_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router)
api_router.include_router(accounts_router)
api_router.include_router(sync_router)
api_router.include_router(threads_router)
api_router.include_router(emails_router)
api_router.include_router(analysis_router)

@api_router.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "email-assistant-api",
        "version": "v1",
    }


@api_router.get("/health/db", tags=["Health"])
async def database_health_check() -> dict[str, str]:
    async with SessionLocal() as session:
        result = await session.execute(text("SELECT 1"))
        result.scalar_one()

    return {
        "status": "healthy",
        "database": "connected",
    }
