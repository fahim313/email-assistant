import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.drafts.models import ReplyDraft
from app.modules.sending.models import EmailSend


async def get_owned_send(
    session: AsyncSession,
    user_id: uuid.UUID,
    send_id: uuid.UUID,
) -> EmailSend | None:
    result = await session.execute(
        select(EmailSend)
        .where(
            EmailSend.id == send_id,
            EmailSend.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )

    return result.scalar_one_or_none()


async def get_send_by_key(
    session: AsyncSession,
    user_id: uuid.UUID,
    idempotency_key: str,
) -> EmailSend | None:
    result = await session.execute(
        select(EmailSend)
        .where(
            EmailSend.user_id == user_id,
            EmailSend.idempotency_key == idempotency_key,
        )
        .execution_options(populate_existing=True)
    )

    return result.scalar_one_or_none()


async def get_send_for_update(
    session: AsyncSession,
    user_id: uuid.UUID,
    send_id: uuid.UUID,
) -> EmailSend | None:
    result = await session.execute(
        select(EmailSend)
        .where(
            EmailSend.id == send_id,
            EmailSend.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    return result.scalar_one_or_none()


async def settle_stale_sends(
    session: AsyncSession,
    draft_id: uuid.UUID,
    cutoff: datetime,
) -> None:
    _ = await session.execute(
        update(EmailSend)
        .where(
            EmailSend.draft_id == draft_id,
            EmailSend.status == "pending",
            EmailSend.updated_at < cutoff,
        )
        .values(
            status="outcome_unknown",
            outcome_unknown=True,
            error_message="The previous attempt did not finish.",
        )
    )


async def has_pending_send(
    session: AsyncSession,
    draft_id: uuid.UUID,
) -> bool:
    result = await session.scalar(
        select(EmailSend.id)
        .where(
            EmailSend.draft_id == draft_id,
            EmailSend.status == "pending",
        )
        .limit(1)
    )

    return result is not None


async def list_sends(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    status: str | None,
    page: int,
    page_size: int,
) -> tuple[list[EmailSend], int]:
    conditions: list[ColumnElement[bool]] = [
        EmailSend.user_id == user_id,
    ]

    if account_id is not None:
        conditions.append(EmailSend.account_id == account_id)

    if status is not None:
        conditions.append(EmailSend.status == status)

    total = await session.scalar(
        select(func.count(EmailSend.id)).where(*conditions)
    )

    result = await session.execute(
        select(EmailSend)
        .where(*conditions)
        .order_by(
            EmailSend.created_at.desc(),
            EmailSend.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    return list(result.scalars().all()), total or 0


def apply_snapshot(
    send: EmailSend,
    draft: ReplyDraft,
    *,
    in_reply_to: str | None,
    references: str | None,
    gmail_thread_id: str | None,
) -> None:
    send.draft_version = draft.current_version
    send.to_addresses = list(draft.to_addresses)
    send.cc_addresses = list(draft.cc_addresses)
    send.bcc_addresses = list(draft.bcc_addresses)
    send.subject = draft.subject
    send.body = draft.body
    send.in_reply_to_header = in_reply_to
    send.references_header = references
    send.gmail_thread_id = gmail_thread_id


async def create_send(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    draft: ReplyDraft,
    idempotency_key: str,
    in_reply_to: str | None,
    references: str | None,
    gmail_thread_id: str | None,
) -> EmailSend:
    send = EmailSend(
        user_id=user_id,
        account_id=draft.account_id,
        draft_id=draft.id,
        idempotency_key=idempotency_key,
        status="pending",
        outcome_unknown=False,
        attempts=1,
    )

    apply_snapshot(
        send,
        draft,
        in_reply_to=in_reply_to,
        references=references,
        gmail_thread_id=gmail_thread_id,
    )

    session.add(send)
    await session.flush()

    return send