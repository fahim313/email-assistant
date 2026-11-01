import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.modules.accounts.models import OAuthState
from app.modules.analysis import service as analysis_service
from app.modules.auth.service import purge_expired_tokens
from app.modules.sync import repository as sync_repository
from app.modules.sync.service import run_sync

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SYNCS = 3
HOUSEKEEPING_EVERY_SECONDS = 3600


async def run_periodic_sync_once() -> int:
    """Incrementally sync every healthy account that is due. Returns count."""
    settings = get_settings()

    async with SessionLocal() as session:
        due = await sync_repository.accounts_due(
            session, timedelta(minutes=settings.SYNC_INTERVAL_MINUTES)
        )

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SYNCS)

    async def _one(account_id: uuid.UUID) -> None:
        async with semaphore:
            try:
                await run_sync(account_id)
            except Exception:
                logger.exception("Periodic sync crashed for %s", account_id)

    _ = await asyncio.gather(*(_one(a) for a in due))
    return len(due)


async def periodic_sync_loop() -> None:
    interval = get_settings().SYNC_INTERVAL_MINUTES
    if interval <= 0:
        logger.info("Periodic sync disabled (SYNC_INTERVAL_MINUTES=0)")
        return

    while True:
        await asyncio.sleep(interval * 60)
        try:
            count = await run_periodic_sync_once()
            logger.info("Periodic sync finished for %d account(s)", count)
        except Exception:
            logger.exception("Periodic sync iteration failed")


async def run_housekeeping_once(session: AsyncSession) -> None:
    _ = await purge_expired_tokens(session)
    _ = await sync_repository.purge_old_runs(session)
    _ = await session.execute(
        delete(OAuthState).where(OAuthState.expires_at < datetime.now(UTC))
    )
    await session.commit()


async def housekeeping_loop() -> None:
    while True:
        try:
            async with SessionLocal() as session:
                await run_housekeeping_once(session)
        except Exception:
            logger.exception("Housekeeping iteration failed")
        await asyncio.sleep(HOUSEKEEPING_EVERY_SECONDS)


async def analysis_loop() -> None:
    settings = get_settings()
    interval = settings.ANALYSIS_INTERVAL_MINUTES

    if interval <= 0:
        logger.info("Analysis loop disabled (ANALYSIS_INTERVAL_MINUTES=0)")
        return

    while True:
        try:
            await analysis_service.run_batch(
                None,
                settings.ANALYSIS_BATCH_LIMIT,
            )
        except Exception:
            logger.exception("Analysis loop failed")

        await asyncio.sleep(interval * 60)