import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions.handlers import register_exception_handlers
from app.workers.tasks import analysis_loop, housekeeping_loop, periodic_sync_loop

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    sync_task = asyncio.create_task(periodic_sync_loop())
    housekeeping_task = asyncio.create_task(housekeeping_loop())
    analysis_task = asyncio.create_task(analysis_loop())

    tasks = (sync_task, housekeeping_task, analysis_task)

    try:
        yield
    finally:
        for task in tasks:
            _ = task.cancel()

        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
)

register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)