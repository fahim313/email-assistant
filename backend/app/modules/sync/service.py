import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from sqlalchemy import or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_token, encrypt_token
from app.db.session import SessionLocal
from app.modules.accounts.models import ConnectedAccount
from app.modules.emails.models import Email
from app.modules.gmail import client as gmail

logger = logging.getLogger(__name__)

SYNC_QUERY = "newer_than:30d"
MAX_MESSAGES = 500
BATCH_SIZE = 50
# A "syncing" row not touched for this long is considered stuck
STALE_AFTER = timedelta(minutes=10)


async def _claim(session: AsyncSession, account_id: uuid.UUID) -> bool:
    """Atomically mark the account as syncing. False if already running."""
    result = await session.execute(
        update(ConnectedAccount)
        .where(
            ConnectedAccount.id == account_id,
            or_(
                ConnectedAccount.sync_status != "syncing",
                ConnectedAccount.updated_at
                < datetime.now(UTC) - STALE_AFTER,
            ),
        )
        .values(
            sync_status="syncing",
            sync_error=None,
            sync_done=0,
            sync_total=0,
        )
        .returning(ConnectedAccount.id)
    )
    claimed = result.scalar_one_or_none() is not None
    await session.commit()
    return claimed


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, RefreshError):
        return "Google access expired or was revoked. Please reconnect this Gmail."
    if isinstance(exc, HttpError):
        status_code = getattr(exc.resp, "status", "?")
        if status_code in (401, 403):
            return (
                f"Gmail refused access ({status_code}). Check that the Gmail "
                "API is enabled and all permissions were granted."
            )
        return f"Gmail API error ({status_code})."
    if isinstance(exc, ValueError):
        return str(exc)[:500]
    return f"Sync failed ({type(exc).__name__})."


async def _mark_failed(
    session: AsyncSession, account_id: uuid.UUID, message: str
) -> None:
    await session.execute(
        update(ConnectedAccount)
        .where(ConnectedAccount.id == account_id)
        .values(sync_status="failed", sync_error=message[:500])
    )
    await session.commit()


async def _upsert_emails(
    session: AsyncSession,
    account_id: uuid.UUID,
    messages: list[dict[str, Any]],
) -> None:
    rows = [
        {"id": uuid.uuid4(), "account_id": account_id, **message}
        for message in messages
    ]
    stmt = pg_insert(Email).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_email_account_message",
        set_={
            "labels": stmt.excluded.labels,
            "is_read": stmt.excluded.is_read,
            "snippet": stmt.excluded.snippet,
        },
    )
    await session.execute(stmt)


async def _sync(session: AsyncSession, account: ConnectedAccount) -> None:
    access_token = (
        decrypt_token(account.access_token_encrypted)
        if account.access_token_encrypted
        else None
    )
    refresh_token = (
        decrypt_token(account.refresh_token_encrypted)
        if account.refresh_token_encrypted
        else None
    )

    creds = gmail.build_credentials(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=account.token_expires_at,
        scopes=account.scopes.split() if account.scopes else [],
    )

    if await asyncio.to_thread(gmail.refresh_if_needed, creds):
        account.access_token_encrypted = encrypt_token(str(creds.token))
        account.token_expires_at = gmail.credentials_expiry(creds)
        await session.commit()

    service = await asyncio.to_thread(gmail.build_service, creds)

    # Read the historyId BEFORE listing so incremental sync won't miss mail
    profile = await asyncio.to_thread(gmail.get_profile, service)
    ids = await asyncio.to_thread(
        gmail.list_message_ids, service, SYNC_QUERY, MAX_MESSAGES
    )

    account.sync_total = len(ids)
    await session.commit()

    for start in range(0, len(ids), BATCH_SIZE):
        chunk = ids[start : start + BATCH_SIZE]
        messages = await asyncio.to_thread(
            gmail.fetch_messages, service, chunk
        )
        if messages:
            await _upsert_emails(session, account.id, messages)
        account.sync_done = min(start + len(chunk), len(ids))
        await session.commit()

    account.history_id = str(profile.get("historyId", "")) or None
    account.sync_status = "synced"
    account.sync_error = None
    account.last_synced_at = datetime.now(UTC)
    await session.commit()


async def run_sync(account_id: uuid.UUID) -> None:
    """Background task: pull the last 30 days of mail for one account."""
    async with SessionLocal() as session:
        if not await _claim(session, account_id):
            return

        account = await session.get(ConnectedAccount, account_id)
        if account is None:
            return

        try:
            await _sync(session, account)
        except Exception as exc:
            await session.rollback()
            logger.exception("Gmail sync failed for account %s", account_id)
            await _mark_failed(session, account_id, _friendly_error(exc))
