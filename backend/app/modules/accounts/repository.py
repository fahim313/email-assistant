import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.models import ConnectedAccount


async def get_by_google_sub(
    session: AsyncSession,
    user_id: uuid.UUID,
    google_sub: str,
) -> ConnectedAccount | None:
    result = await session.execute(
        select(ConnectedAccount).where(
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.google_sub == google_sub,
        )
    )
    return result.scalar_one_or_none()


async def get_by_id(
    session: AsyncSession,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ConnectedAccount | None:
    result = await session.execute(
        select(ConnectedAccount).where(
            ConnectedAccount.id == account_id,
            ConnectedAccount.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_by_user(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> list[ConnectedAccount]:
    result = await session.execute(
        select(ConnectedAccount)
        .where(ConnectedAccount.user_id == user_id)
        .order_by(ConnectedAccount.created_at)
    )
    return list(result.scalars().all())


async def save(
    session: AsyncSession,
    account: ConnectedAccount,
) -> ConnectedAccount:
    session.add(account)
    await session.flush()
    return account
