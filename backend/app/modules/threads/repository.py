import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict, cast

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.accounts.models import ConnectedAccount
from app.modules.emails.models import Email
from app.modules.threads.models import Thread

MAX_PARTICIPANTS = 20
REBUILD_CHUNK = 200


class _MessageRow(TypedDict):
    account_id: uuid.UUID
    gmail_thread_id: str
    subject: str
    snippet: str
    first_message_at: datetime
    last_message_at: datetime
    message_count: int
    last_sender_name: str
    last_sender_email: str
    last_message_is_sent: bool
    has_inbox: bool
    is_unread: bool
    participants: list[str]


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
) -> _MessageRow:
    msgs = sorted(msgs, key=lambda message: message.received_at)
    first, last = msgs[0], msgs[-1]

    subject = next(
        (message.subject for message in msgs if message.subject),
        "",
    )

    participants: list[str] = []

    for message in msgs:
        addresses = [message.sender_email]

        if message.to_addresses:
            addresses.extend(
                address.strip()
                for address in message.to_addresses.split(",")
            )

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
        "has_inbox": any(
            "INBOX" in message.labels for message in msgs
        ),
        "is_unread": any(
            not message.is_read and "INBOX" in message.labels
            for message in msgs
        ),
        "participants": participants[:MAX_PARTICIPANTS],
    }


async def rebuild_threads(
    session: AsyncSession,
    account_id: uuid.UUID,
    thread_ids: set[str],
) -> None:
    """Recompute thread rows from messages and remove empty threads."""
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
            message = cast(
                tuple[
                    str,
                    str,
                    str,
                    str,
                    str | None,
                    str,
                    datetime,
                    list[str],
                    bool,
                    bool,
                ],
                row,
            )

            (
                thread_id,
                subject,
                sender_name,
                sender_email,
                to_addresses,
                snippet,
                received_at,
                labels,
                is_read,
                is_sent,
            ) = message

            grouped[thread_id].append(
                _Msg(
                    thread_id=thread_id,
                    subject=subject,
                    sender_name=sender_name,
                    sender_email=sender_email,
                    to_addresses=to_addresses,
                    snippet=snippet,
                    received_at=received_at,
                    labels=list(labels),
                    is_read=is_read,
                    is_sent=is_sent,
                )
            )

        gone = [
            thread_id
            for thread_id in chunk
            if thread_id not in grouped
        ]

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
            {
                "id": uuid.uuid4(),
                **_build_thread_row(account_id, thread_id, messages),
            }
            for thread_id, messages in grouped.items()
        ]

        stmt = pg_insert(Thread).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_thread_account_gmail_thread",
            set_={
                column: getattr(stmt.excluded, column)
                for column in rows[0]
                if column not in (
                    "id",
                    "account_id",
                    "gmail_thread_id",
                )
            },
        )

        _ = await session.execute(stmt)


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
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
    conditions: list[ColumnElement[bool]] = [
        ConnectedAccount.user_id == user_id
    ]

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
                func.array_to_string(
                    Thread.participants,
                    " ",
                ).ilike(like, escape="\\"),
            )
        )

    total = await session.scalar(
        select(func.count(Thread.id))
        .select_from(Thread)
        .join(
            ConnectedAccount,
            Thread.account_id == ConnectedAccount.id,
        )
        .where(*conditions)
    )

    result = await session.execute(
        select(Thread, ConnectedAccount.email)
        .join(
            ConnectedAccount,
            Thread.account_id == ConnectedAccount.id,
        )
        .where(*conditions)
        .order_by(Thread.last_message_at.desc(), Thread.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items = [
        (row[0], row[1])
        for row in result.all()
    ]

    return items, total or 0


async def get_thread(
    session: AsyncSession,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> tuple[Thread, str] | None:
    result = await session.execute(
        select(Thread, ConnectedAccount.email)
        .join(
            ConnectedAccount,
            Thread.account_id == ConnectedAccount.id,
        )
        .where(
            Thread.id == thread_id,
            ConnectedAccount.user_id == user_id,
        )
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