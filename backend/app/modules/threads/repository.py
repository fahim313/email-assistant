import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.models import ConnectedAccount
from app.modules.threads.models import Email, Thread

MAX_PARTICIPANTS = 20
REBUILD_CHUNK = 200


# --- Message writes (used by sync) ----------------------------------------


async def upsert_emails(
    session: AsyncSession,
    account_id: uuid.UUID,
    messages: list[dict[str, Any]],
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
    """Delete messages; returns the thread ids they belonged to."""
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


# --- Thread grouping ------------------------------------------------------


@dataclass
class _Msg:
    thread_id: str
    subject: str
    sender_name: str
    sender_email: str
    to_addresses: str | None
    snippet: str
    received_at: datetime
    labels: list[str]
    is_read: bool
    is_sent: bool


def _build_thread_row(
    account_id: uuid.UUID,
    thread_id: str,
    msgs: list[_Msg],
) -> dict[str, Any]:
    msgs = sorted(msgs, key=lambda m: m.received_at)
    first, last = msgs[0], msgs[-1]

    subject = next((m.subject for m in msgs if m.subject), "")

    participants: list[str] = []
    for m in msgs:
        addresses = [m.sender_email]
        if m.to_addresses:
            addresses += [a.strip() for a in m.to_addresses.split(",")]
        for address in addresses:
            if address and address not in participants:
                participants.append(address)

    return {
        "account_id": account_id,
        "gmail_thread_id": thread_id,
        "subject": subject,
        "snippet": last.snippet,
        "first_message_at": first.received_at,
        "last_message_at": last.received_at,
        "message_count": len(msgs),
        "last_sender_name": last.sender_name,
        "last_sender_email": last.sender_email,
        "last_message_is_sent": last.is_sent,
        "has_inbox": any("INBOX" in m.labels for m in msgs),
        "is_unread": any(
            (not m.is_read) and "INBOX" in m.labels for m in msgs
        ),
        "participants": participants[:MAX_PARTICIPANTS],
    }


async def rebuild_threads(
    session: AsyncSession,
    account_id: uuid.UUID,
    thread_ids: set[str],
) -> None:
    """Recompute the `threads` rows for the given Gmail thread ids from
    their messages. Threads left with no messages are removed."""
    ordered = sorted(thread_ids)

    for start in range(0, len(ordered), REBUILD_CHUNK):
        chunk = ordered[start : start + REBUILD_CHUNK]

        result = await session.execute(
            select(
                Email.thread_id,
                Email.subject,
                Email.sender_name,
                Email.sender_email,
                Email.to_addresses,
                Email.snippet,
                Email.received_at,
                Email.labels,
                Email.is_read,
                Email.is_sent,
            ).where(
                Email.account_id == account_id,
                Email.thread_id.in_(chunk),
            )
        )

        grouped: defaultdict[str, list[_Msg]] = defaultdict(list)
        for row in result.all():
            grouped[row.thread_id].append(
                _Msg(
                    thread_id=row.thread_id,
                    subject=row.subject,
                    sender_name=row.sender_name,
                    sender_email=row.sender_email,
                    to_addresses=row.to_addresses,
                    snippet=row.snippet,
                    received_at=row.received_at,
                    labels=list(row.labels),
                    is_read=row.is_read,
                    is_sent=row.is_sent,
                )
            )

        gone = [t for t in chunk if t not in grouped]
        if gone:
            _ = await session.execute(
                delete(Thread).where(
                    Thread.account_id == account_id,
                    Thread.gmail_thread_id.in_(gone),
                )
            )

        if not grouped:
            continue

        rows = [
            {"id": uuid.uuid4(), **_build_thread_row(account_id, tid, msgs)}
            for tid, msgs in grouped.items()
        ]
        stmt = pg_insert(Thread).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_thread_account_gmail_thread",
            set_={
                column: getattr(stmt.excluded, column)
                for column in rows[0]
                if column not in ("id", "account_id", "gmail_thread_id")
            },
        )
        _ = await session.execute(stmt)


# --- Reads (used by the API) ----------------------------------------------


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


async def list_threads(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    q: str | None,
    unread_only: bool,
    page: int,
    page_size: int,
) -> tuple[list[tuple[Thread, str]], int]:
    # Always scoped to the logged-in user's own accounts (tenant isolation).
    conditions: list[Any] = [ConnectedAccount.user_id == user_id]

    if account_id is not None:
        conditions.append(Thread.account_id == account_id)

    if unread_only:
        conditions.append(Thread.is_unread.is_(True))

    if q:
        like = f"%{_escape_like(q.strip())}%"
        conditions.append(
            or_(
                Thread.subject.ilike(like, escape="\\"),
                Thread.snippet.ilike(like, escape="\\"),
                Thread.last_sender_name.ilike(like, escape="\\"),
                Thread.last_sender_email.ilike(like, escape="\\"),
                func.array_to_string(Thread.participants, " ").ilike(
                    like, escape="\\"
                ),
            )
        )

    total = await session.scalar(
        select(func.count(Thread.id))
        .select_from(Thread)
        .join(ConnectedAccount, Thread.account_id == ConnectedAccount.id)
        .where(*conditions)
    )

    result = await session.execute(
        select(Thread, ConnectedAccount.email)
        .join(ConnectedAccount, Thread.account_id == ConnectedAccount.id)
        .where(*conditions)
        .order_by(Thread.last_message_at.desc(), Thread.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [(row[0], row[1]) for row in result.all()]
    return items, total or 0


async def get_thread(
    session: AsyncSession,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> tuple[Thread, str] | None:
    result = await session.execute(
        select(Thread, ConnectedAccount.email)
        .join(ConnectedAccount, Thread.account_id == ConnectedAccount.id)
        .where(Thread.id == thread_id, ConnectedAccount.user_id == user_id)
    )
    row = result.first()
    return (row[0], row[1]) if row else None


async def list_thread_messages(
    session: AsyncSession,
    thread: Thread,
) -> list[Email]:
    result = await session.execute(
        select(Email)
        .where(
            Email.account_id == thread.account_id,
            Email.thread_id == thread.gmail_thread_id,
        )
        .order_by(Email.received_at, Email.id)
    )
    return list(result.scalars().all())
