import uuid
from collections.abc import Iterable

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.integrations.gmail_client import EmailUpsertData
from app.modules.accounts.models import ConnectedAccount
from app.modules.emails.models import Email
from app.modules.threads.models import Thread


async def upsert_emails(
    session: AsyncSession,
    account_id: uuid.UUID,
    messages: list[EmailUpsertData],
) -> None:
    if not messages:
        return

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
            "is_sent": stmt.excluded.is_sent,
            "snippet": stmt.excluded.snippet,
            "body_text": stmt.excluded.body_text,
            "to_addresses": stmt.excluded.to_addresses,
            "cc_addresses": stmt.excluded.cc_addresses,
            "reply_to": stmt.excluded.reply_to,
            "message_id_header": stmt.excluded.message_id_header,
            "in_reply_to_header": stmt.excluded.in_reply_to_header,
            "references_header": stmt.excluded.references_header,
            "auth_results": stmt.excluded.auth_results,
        },
    )
    _ = await session.execute(stmt)


async def delete_emails(
    session: AsyncSession,
    account_id: uuid.UUID,
    gmail_message_ids: Iterable[str],
) -> set[str]:
    """Delete messages and return their Gmail thread IDs."""
    ids = list(gmail_message_ids)
    if not ids:
        return set()

    result = await session.execute(
        delete(Email)
        .where(
            Email.account_id == account_id,
            Email.gmail_message_id.in_(ids),
        )
        .returning(Email.thread_id)
    )
    return set(result.scalars().all())


async def existing_message_ids(
    session: AsyncSession,
    account_id: uuid.UUID,
    gmail_message_ids: Iterable[str],
) -> set[str]:
    ids = list(gmail_message_ids)
    if not ids:
        return set()

    result = await session.execute(
        select(Email.gmail_message_id).where(
            Email.account_id == account_id,
            Email.gmail_message_id.in_(ids),
        )
    )
    return set(result.scalars().all())


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_emails(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    thread_id: uuid.UUID | None,
    q: str | None,
    unread_only: bool,
    is_sent: bool | None,
    page: int,
    page_size: int,
) -> tuple[list[tuple[Email, str, uuid.UUID | None]], int]:
    conditions: list[ColumnElement[bool]] = [
        ConnectedAccount.user_id == user_id,
    ]

    if account_id is not None:
        conditions.append(Email.account_id == account_id)

    if thread_id is not None:
        conditions.append(Thread.id == thread_id)

    if unread_only:
        conditions.append(Email.is_read.is_(False))

    if is_sent is not None:
        conditions.append(Email.is_sent.is_(is_sent))

    if q:
        like = f"%{_escape_like(q.strip())}%"
        conditions.append(
            or_(
                Email.subject.ilike(like, escape="\\"),
                Email.snippet.ilike(like, escape="\\"),
                Email.sender_name.ilike(like, escape="\\"),
                Email.sender_email.ilike(like, escape="\\"),
            )
        )

    total_stmt = (
        select(func.count(Email.id))
        .select_from(Email)
        .join(
            ConnectedAccount,
            Email.account_id == ConnectedAccount.id,
        )
        .outerjoin(
            Thread,
            and_(
                Thread.account_id == Email.account_id,
                Thread.gmail_thread_id == Email.thread_id,
            ),
        )
        .where(*conditions)
    )
    total = await session.scalar(total_stmt)

    result = await session.execute(
        select(Email, ConnectedAccount.email, Thread.id)
        .select_from(Email)
        .join(
            ConnectedAccount,
            Email.account_id == ConnectedAccount.id,
        )
        .outerjoin(
            Thread,
            and_(
                Thread.account_id == Email.account_id,
                Thread.gmail_thread_id == Email.thread_id,
            ),
        )
        .where(*conditions)
        .order_by(Email.received_at.desc(), Email.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items: list[tuple[Email, str, uuid.UUID | None]] = [
        (email, account_email, db_thread_id)
        for email, account_email, db_thread_id in result.all()
    ]

    return items, total or 0


async def get_email(
    session: AsyncSession,
    user_id: uuid.UUID,
    email_id: uuid.UUID,
) -> tuple[Email, str, uuid.UUID | None] | None:
    result = await session.execute(
        select(Email, ConnectedAccount.email, Thread.id)
        .select_from(Email)
        .join(
            ConnectedAccount,
            Email.account_id == ConnectedAccount.id,
        )
        .outerjoin(
            Thread,
            and_(
                Thread.account_id == Email.account_id,
                Thread.gmail_thread_id == Email.thread_id,
            ),
        )
        .where(
            Email.id == email_id,
            ConnectedAccount.user_id == user_id,
        )
    )

    row = result.one_or_none()
    if row is None:
        return None

    email, account_email, db_thread_id = row
    return email, account_email, db_thread_id
