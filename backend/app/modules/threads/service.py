import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions.error import NotFoundError
from app.modules.analysis.models import ThreadAnalysis
from app.modules.analysis.schemas import AnalysisBrief
from app.modules.emails.models import Email
from app.modules.threads import repository
from app.modules.threads.models import Thread
from app.modules.threads.schemas import (
    MessageItem,
    ThreadDetail,
    ThreadItem,
    ThreadPage,
)


def _split(addresses: str | None) -> list[str]:
    if not addresses:
        return []

    return [address.strip() for address in addresses.split(",")]


def _brief(analysis: ThreadAnalysis | None) -> AnalysisBrief | None:
    if analysis is None:
        return None

    return AnalysisBrief.model_validate(analysis)


def _thread_item(
    thread: Thread,
    account_email: str,
    analysis: ThreadAnalysis | None,
) -> ThreadItem:
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
        analysis=_brief(analysis),
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
    priority: str | None,
    category: str | None,
    is_spam: bool | None,
    needs_reply: bool | None,
    follow_up: bool | None,
    has_deadline: bool | None,
    page: int,
    page_size: int,
) -> ThreadPage:
    rows, total = await repository.list_threads(
        session,
        user_id,
        account_id=account_id,
        q=q,
        unread_only=unread_only,
        priority=priority,
        category=category,
        is_spam=is_spam,
        needs_reply=needs_reply,
        follow_up=follow_up,
        has_deadline=has_deadline,
        page=page,
        page_size=page_size,
    )

    return ThreadPage(
        items=[
            _thread_item(thread, account_email, analysis)
            for thread, account_email, analysis in rows
        ],
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
    analysis = await repository.get_thread_analysis(session, thread.id)

    return ThreadDetail(
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
        analysis=_brief(analysis),
        messages=[_message_item(message) for message in messages],
    )