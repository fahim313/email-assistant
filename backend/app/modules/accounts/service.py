import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_token
from app.modules.accounts import repository
from app.modules.accounts.models import ConnectedAccount


async def save_google_account(
    session: AsyncSession,
    user_id: uuid.UUID,
    google_sub: str,
    email: str,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
    scopes: list[str],
) -> ConnectedAccount:
    account = await repository.get_by_google_sub(session, user_id, google_sub)
    is_new = account is None
    
    if is_new:
        account = ConnectedAccount(
            user_id=user_id,
            google_sub=google_sub,
            email=email,
        )
    else:
        account.email = email

    account.access_token_encrypted = encrypt_token(access_token)

    if refresh_token:
        account.refresh_token_encrypted = encrypt_token(refresh_token)

    account.token_expires_at = expires_at
    account.scopes = " ".join(scopes)
    account.sync_status = "pending"

    if is_new:
        account = await repository.save(session, account)
    else:
        await session.flush()

    await session.commit()
    await session.refresh(account)

    return account