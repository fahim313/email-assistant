import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.models import ConnectedAccount
from app.modules.sync.models import SyncRun


async def claim(
    session: AsyncSession,
    account_id: uuid.UUID,
    stale_after: timedelta,
) -> bool:
    """Atomically mark the account as syncing. False if a sync is already
    running (a row untouched for `stale_after` is considered crashed)."""
    result = await session.execute(
        update(ConnectedAccount)
        .where(
            ConnectedAccount.id == account_id,
            or_(
                ConnectedAccount.sync_status != "syncing",
                ConnectedAccount.updated_at < datetime.now(UTC) - stale_after,
            ),
        )
        .values(
            sync_status="syncing",
            sync_step="connecting",
            sync_error=None,
            sync_done=0,
            sync_total=0,
        )
        .returning(ConnectedAccount.id)
    )
    claimed = result.scalar_one_or_none() is not None
    await session.commit()
    return claimed


async def mark_failed(
    session: AsyncSession,
    account_id: uuid.UUID,
    message: str,
) -> None:
    _ = await session.execute(
        update(ConnectedAccount)
        .where(ConnectedAccount.id == account_id)
        .values(sync_status="failed", sync_error=message[:500])
    )
    await session.commit()


async def accounts_due(
    session: AsyncSession,
    older_than: timedelta,
    limit: int = 200,
) -> list[uuid.UUID]:
    """Accounts that are healthy, idle, and not synced recently."""
    cutoff = datetime.now(UTC) - older_than
    result = await session.execute(
        select(ConnectedAccount.id)
        .where(
            ConnectedAccount.needs_reauth.is_(False),
            ConnectedAccount.sync_status != "syncing",
            or_(
                ConnectedAccount.last_synced_at.is_(None),
                ConnectedAccount.last_synced_at < cutoff,
            ),
        )
        .order_by(ConnectedAccount.last_synced_at.asc().nulls_first())
        .limit(limit)
    )
    return list(result.scalars().all())


async def purge_old_runs(session: AsyncSession, keep_days: int = 30) -> int:
    result = await session.execute(
        delete(SyncRun).where(
            SyncRun.started_at < datetime.now(UTC) - timedelta(days=keep_days)
        )
    )
    await session.commit()
    return result.rowcount if isinstance(result, CursorResult) else 0  


async def finish_run(
    session: AsyncSession,
    run_id: uuid.UUID,
    status: str,
    error: str | None = None,
) -> None:
    _ = await session.execute(
        update(SyncRun)
        .where(SyncRun.id == run_id)
        .values(
            status=status,
            error=error[:500] if error else None,
            finished_at=datetime.now(UTC),
        )
    )
    await session.commit()
