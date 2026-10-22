import asyncio
import logging
import uuid
from datetime import datetime

from cryptography.fernet import InvalidToken
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions.error import NotFoundError, ReconnectRequiredError
from app.core.security import decrypt_token, encrypt_token
from app.integrations import gmail_client as gmail
from app.modules.accounts import repository
from app.modules.accounts.google_oauth import revoke_google_token
from app.modules.accounts.models import ConnectedAccount

logger = logging.getLogger(__name__)


async def get_owned_account(
    session: AsyncSession,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ConnectedAccount:
    account = await repository.get_by_id(session, account_id, user_id)
    if account is None:
        raise NotFoundError("connected_account", str(account_id))
    return account


async def save_google_account(
    session: AsyncSession,
    user_id: uuid.UUID,
    google_sub: str,
    email: str,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
    scopes: list[str],
) -> tuple[ConnectedAccount, bool]:
    """Create the account, or refresh tokens if this Gmail is already
    connected for the user (so the same Gmail is never stored twice).

    Returns (account, created).
    """
    account = await repository.get_by_google_sub(session, user_id, google_sub)
    is_new = account is None

    if account is None:
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
    account.needs_reauth = False

    if is_new:
        account.sync_status = "pending"
        account = await repository.save(session, account)
    else:
        await session.flush()

    await session.commit()
    await session.refresh(account)

    return account, is_new


async def get_credentials(
    session: AsyncSession,
    account: ConnectedAccount,
) -> Credentials:
    """Decrypt stored tokens and auto-refresh an expired access token.

    The refreshed token is re-encrypted and saved. If Google rejects the
    refresh token (revoked, expired, removed) the account is flagged
    `needs_reauth` and ReconnectRequiredError is raised.
    """
    if account.needs_reauth:
        raise ReconnectRequiredError(
            "This Gmail account was disconnected. Please reconnect it."
        )

    creds = gmail.build_credentials(
        access_token=(
            decrypt_token(account.access_token_encrypted)
            if account.access_token_encrypted
            else None
        ),
        refresh_token=(
            decrypt_token(account.refresh_token_encrypted)
            if account.refresh_token_encrypted
            else None
        ),
        expires_at=account.token_expires_at,
        scopes=account.scopes.split() if account.scopes else [],
    )

    try:
        refreshed = await asyncio.to_thread(gmail.refresh_if_needed, creds)
    except (RefreshError, ValueError):
        account.needs_reauth = True
        await session.commit()
        raise ReconnectRequiredError(
            "Google access expired or was revoked. Please reconnect this Gmail."
        ) from None

    if refreshed:
        account.access_token_encrypted = encrypt_token(str(creds.token))
        account.token_expires_at = gmail.credentials_expiry(creds)
        await session.commit()

    return creds


async def disconnect_account(
    session: AsyncSession,
    account: ConnectedAccount,
) -> None:
    """Remove the account. Its emails, threads and sync history go with it
    (ON DELETE CASCADE); the user's login is untouched."""
    token_to_revoke: str | None = None
    try:
        if account.refresh_token_encrypted:
            token_to_revoke = decrypt_token(account.refresh_token_encrypted)
        elif account.access_token_encrypted:
            token_to_revoke = decrypt_token(account.access_token_encrypted)
    except (InvalidToken, ValueError):
        logger.warning("Could not decrypt token for revoke on disconnect.")

    try:
        await session.delete(account)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    # Only after our data is gone: tell Google to drop the grant.
    if token_to_revoke:
        revoked = await revoke_google_token(token_to_revoke)
        if not revoked:
            logger.warning("Google token revoke did not succeed.")
