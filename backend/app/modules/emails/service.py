import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions.error import NotFoundError
from app.modules.emails import repository
from app.modules.emails.models import Email
from app.modules.emails.schemas import EmailDetail, EmailItem, EmailPage


def _split(addresses: str | None) -> list[str]:
    return [address.strip() for address in addresses.split(",")] if addresses else []


def _email_item(
    email: Email,
    account_email: str,
    thread_id: uuid.UUID | None,
) -> EmailItem:
    return EmailItem(
        id=email.id,
        account_id=email.account_id,
        account_email=account_email,
        thread_id=thread_id,
        subject=email.subject,
        sender_name=email.sender_name,
        sender_email=email.sender_email,
        snippet=email.snippet,
        received_at=email.received_at,
        labels=list(email.labels),
        is_read=email.is_read,
        is_sent=email.is_sent,
    )


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
) -> EmailPage:
    rows, total = await repository.list_emails(
        session,
        user_id,
        account_id=account_id,
        thread_id=thread_id,
        q=q,
        unread_only=unread_only,
        is_sent=is_sent,
        page=page,
        page_size=page_size,
    )

    return EmailPage(
        items=[
            _email_item(email, account_email, db_thread_id)
            for email, account_email, db_thread_id in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_email_detail(
    session: AsyncSession,
    user_id: uuid.UUID,
    email_id: uuid.UUID,
) -> EmailDetail:
    found = await repository.get_email(session, user_id, email_id)

    if found is None:
        raise NotFoundError("email", str(email_id))

    email, account_email, thread_id = found

    return EmailDetail(
        id=email.id,
        account_id=email.account_id,
        account_email=account_email,
        thread_id=thread_id,
        subject=email.subject,
        sender_name=email.sender_name,
        sender_email=email.sender_email,
        snippet=email.snippet,
        received_at=email.received_at,
        labels=list(email.labels),
        is_read=email.is_read,
        is_sent=email.is_sent,
        to=_split(email.to_addresses),
        cc=_split(email.cc_addresses),
        reply_to=email.reply_to,
        body_text=email.body_text,
    )
