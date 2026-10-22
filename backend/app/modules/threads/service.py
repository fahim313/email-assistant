import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions.error import NotFoundError
from app.modules.threads import repository
from app.modules.threads.models import Email, Thread
from app.modules.threads.schemas import (
    MessageItem,
    ThreadDetail,
    ThreadItem,
    ThreadPage,
)


def _split(addresses: str | None) -> list[str]:
    return [a.strip() for a in addresses.split(",")] if addresses else []


def _thread_item(thread: Thread, account_email: str) -> ThreadItem:
    return ThreadItem(
        id=thread.id,
        account_id=thread.account_id,
        account_email=account_email,
        subject=thread.subject,
        snippet=thread.snippet,
        last_message_at=thread.last_message_at,
        message_count=thread.message_count,
        last_sender_name=thread.last_sender_name,
        last_sender_email=thread.last_sender_email,
        last_message_is_sent=thread.last_message_is_sent,
        is_unread=thread.is_unread,
        participants=list(thread.participants),
    )


def _message_item(email: Email) -> MessageItem:
    return MessageItem(
        id=email.id,
        subject=email.subject,
        sender_name=email.sender_name,
        sender_email=email.sender_email,
        to=_split(email.to_addresses),
        cc=_split(email.cc_addresses),
        snippet=email.snippet,
        body_text=email.body_text,
        received_at=email.received_at,
        is_read=email.is_read,
        is_sent=email.is_sent,
        labels=list(email.labels),
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
) -> ThreadPage:
    rows, total = await repository.list_threads(
        session,
        user_id,
        account_id=account_id,
        q=q,
        unread_only=unread_only,
        page=page,
        page_size=page_size,
    )
    return ThreadPage(
        items=[_thread_item(t, email) for t, email in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_thread_detail(
    session: AsyncSession,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> ThreadDetail:
    found = await repository.get_thread(session, user_id, thread_id)
    if found is None:
        raise NotFoundError("thread", str(thread_id))

    thread, account_email = found
    messages = await repository.list_thread_messages(session, thread)

    return ThreadDetail(
        **_thread_item(thread, account_email).model_dump(),
        messages=[_message_item(m) for m in messages],
    )
