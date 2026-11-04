import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from email.utils import getaddresses
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException
from groq import APIError
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions.error import NotFoundError
from app.modules.accounts.models import ConnectedAccount
from app.modules.ai.llm import get_structured_llm
from app.modules.ai.prompts.reply_generation import (
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.modules.analysis import cleaning
from app.modules.analysis import repository as analysis_repository
from app.modules.drafts import repository
from app.modules.drafts.models import ReplyDraft, ReplyDraftVersion
from app.modules.drafts.schemas import (
    MAX_ADDRESSES,
    MAX_BODY_CHARS,
    DraftCreate,
    DraftDetail,
    DraftItem,
    DraftPage,
    DraftUpdate,
    DraftVersionItem,
    ReplyOutput,
)
from app.modules.emails.models import Email
from app.modules.threads import repository as thread_repository
from app.modules.threads.models import Thread

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class _Context:
    account: ConnectedAccount
    messages: list[Email]
    thread_text: str
    summary: str | None


def _clean_instruction(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


def _reply_subject(subject: str) -> str:
    value = subject.strip()

    if not value:
        return "Re:"

    if value[:3].lower() == "re:":
        return value[:998]

    return f"Re: {value}"[:998]


def _addresses(raw: str | None) -> list[str]:
    if not raw:
        return []

    return [
        address.strip()
        for _, address in getaddresses([raw])
        if address.strip()
    ]


def _recipients(messages: list[Email], owner_email: str) -> list[str]:
    last = messages[-1]

    raw = (
        last.to_addresses
        if last.is_sent
        else last.reply_to or last.sender_email
    )

    owner = owner_email.strip().lower()
    seen: set[str] = set()
    result: list[str] = []

    for address in _addresses(raw):
        key = address.lower()

        if key == owner or key in seen:
            continue

        seen.add(key)
        result.append(address)

    return result[:MAX_ADDRESSES]


def _version_item(version: ReplyDraftVersion) -> DraftVersionItem:
    return DraftVersionItem.model_validate(
        {
            "id": version.id,
            "version_number": version.version_number,
            "source": version.source,
            "instruction": version.instruction,
            "model": version.model,
            "subject": version.subject,
            "body": version.body,
            "to": list(version.to_addresses),
            "cc": list(version.cc_addresses),
            "bcc": list(version.bcc_addresses),
            "created_at": version.created_at,
        }
    )


def _draft_item(draft: ReplyDraft) -> DraftItem:
    return DraftItem.model_validate(
        {
            "id": draft.id,
            "account_id": draft.account_id,
            "thread_id": draft.thread_id,
            "reply_to_email_id": draft.reply_to_email_id,
            "to": list(draft.to_addresses),
            "cc": list(draft.cc_addresses),
            "bcc": list(draft.bcc_addresses),
            "subject": draft.subject,
            "body": draft.body,
            "status": draft.status,
            "source": draft.source,
            "current_version": draft.current_version,
            "gmail_draft_id": draft.gmail_draft_id,
            "gmail_sync_status": draft.gmail_sync_status,
            "gmail_sync_error": draft.gmail_sync_error,
            "model": draft.model,
            "sent_at": draft.sent_at,
            "gmail_message_id": draft.gmail_message_id,
            "created_at": draft.created_at,
            "updated_at": draft.updated_at,
        }
    )


def _draft_detail(
    draft: ReplyDraft,
    versions: list[ReplyDraftVersion],
) -> DraftDetail:
    item = _draft_item(draft)

    return DraftDetail(
        id=item.id,
        account_id=item.account_id,
        thread_id=item.thread_id,
        reply_to_email_id=item.reply_to_email_id,
        to=item.to,
        cc=item.cc,
        bcc=item.bcc,
        subject=item.subject,
        body=item.body,
        status=item.status,
        source=item.source,
        current_version=item.current_version,
        gmail_draft_id=item.gmail_draft_id,
        gmail_sync_status=item.gmail_sync_status,
        gmail_sync_error=item.gmail_sync_error,
        model=item.model,
        sent_at=item.sent_at,
        gmail_message_id=item.gmail_message_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
        versions=[_version_item(version) for version in versions],
    )


async def _finish(
    session: AsyncSession,
    draft: ReplyDraft,
) -> DraftDetail:
    await session.commit()
    await session.refresh(draft)

    versions = await repository.list_versions(session, draft.id)

    return _draft_detail(draft, versions)


async def _load_context(
    session: AsyncSession,
    thread: Thread,
) -> _Context:
    account = await session.get(ConnectedAccount, thread.account_id)

    if account is None:
        raise NotFoundError("account", str(thread.account_id))

    messages = await thread_repository.list_thread_messages(
        session,
        thread,
    )

    if not messages:
        raise HTTPException(
            status_code=409,
            detail="Thread has no messages to reply to",
        )

    cleaned = cleaning.build_thread_text(
        thread.subject,
        messages,
        get_settings().DRAFT_MAX_INPUT_CHARS,
    )

    analysis = await analysis_repository.get_analysis(
        session,
        thread.id,
    )

    summary = (
        analysis.summary
        if analysis is not None and analysis.status == "done"
        else None
    )

    return _Context(
        account=account,
        messages=messages,
        thread_text=cleaned.text,
        summary=summary,
    )


async def _generate_body(
    context: _Context,
    *,
    instruction: str | None,
    previous_body: str | None,
) -> str:
    settings = get_settings()
    timezone_name = str(settings.APP_TIMEZONE)
    now = datetime.now(ZoneInfo(timezone_name))

    messages: list[BaseMessage] = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=build_user_prompt(
                context.thread_text,
                now,
                timezone_name,
                context.account.email,
                instruction,
                context.summary,
                previous_body,
            )
        ),
    ]

    last_error = "Reply was empty"

    for attempt in range(MAX_ATTEMPTS):
        if attempt > 0:
            await asyncio.sleep(2.0**attempt)

        try:
            raw = await get_structured_llm(ReplyOutput).ainvoke(messages)
            output = ReplyOutput.model_validate(raw)

        except (
            APIError,
            httpx.HTTPError,
            TimeoutError,
            ValidationError,
        ) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue

        body = output.body.strip()[:MAX_BODY_CHARS]

        if body:
            return body

        last_error = "Reply was empty"

    logger.error("Reply generation failed: %s", last_error)

    raise HTTPException(
        status_code=502,
        detail="Reply generation failed",
    )


async def generate_draft(
    session: AsyncSession,
    thread: Thread,
    instruction: str | None,
) -> DraftDetail:
    cleaned_instruction = _clean_instruction(instruction)
    context = await _load_context(session, thread)

    body = await _generate_body(
        context,
        instruction=cleaned_instruction,
        previous_body=None,
    )

    model = get_settings().LLM_MODEL

    draft = await repository.create_draft(
        session,
        account_id=thread.account_id,
        thread_id=thread.id,
        reply_to_email_id=context.messages[-1].id,
        to=_recipients(context.messages, context.account.email),
        cc=[],
        bcc=[],
        subject=_reply_subject(thread.subject),
        body=body,
        source="ai",
        model=model,
    )

    _ = await repository.add_version(
        session,
        draft,
        source="ai_generate",
        instruction=cleaned_instruction,
        model=model,
    )

    return await _finish(session, draft)


async def regenerate_draft(
    session: AsyncSession,
    draft: ReplyDraft,
    instruction: str | None,
) -> DraftDetail:
    if draft.thread_id is None:
        raise HTTPException(
            status_code=409,
            detail="Draft is not linked to a thread",
        )

    thread = await session.get(Thread, draft.thread_id)

    if thread is None:
        raise NotFoundError("thread", str(draft.thread_id))

    cleaned_instruction = _clean_instruction(instruction)
    context = await _load_context(session, thread)

    body = await _generate_body(
        context,
        instruction=cleaned_instruction,
        previous_body=draft.body or None,
    )

    model = get_settings().LLM_MODEL

    await session.refresh(draft, with_for_update=True)

    if draft.status == "sent":
        raise HTTPException(
            status_code=409,
            detail="A sent draft cannot be regenerated",
        )

    draft.body = body
    draft.model = model
    draft.current_version += 1
    draft.gmail_sync_status = "pending"
    draft.gmail_sync_error = None

    _ = await repository.add_version(
        session,
        draft,
        source="ai_regenerate",
        instruction=cleaned_instruction,
        model=model,
    )

    return await _finish(session, draft)


async def create_draft(
    session: AsyncSession,
    user_id: uuid.UUID,
    data: DraftCreate,
) -> DraftDetail:
    account = await analysis_repository.get_owned_account(
        session,
        user_id,
        data.account_id,
    )

    if account is None:
        raise NotFoundError("account", str(data.account_id))

    thread_id: uuid.UUID | None = None
    reply_to_email_id: uuid.UUID | None = None

    if data.thread_id is not None:
        thread = await analysis_repository.get_owned_thread(
            session,
            user_id,
            data.thread_id,
        )

        if thread is None:
            raise NotFoundError("thread", str(data.thread_id))

        if thread.account_id != account.id:
            raise HTTPException(
                status_code=422,
                detail="The thread does not belong to this account",
            )

        messages = await thread_repository.list_thread_messages(
            session,
            thread,
        )

        thread_id = thread.id

        if data.reply_to_email_id is not None:
            target = next(
                (
                    message
                    for message in messages
                    if message.id == data.reply_to_email_id
                ),
                None,
            )

            if target is None:
                raise NotFoundError("email", str(data.reply_to_email_id))

            reply_to_email_id = target.id

        elif messages:
            reply_to_email_id = messages[-1].id

    draft = await repository.create_draft(
        session,
        account_id=account.id,
        thread_id=thread_id,
        reply_to_email_id=reply_to_email_id,
        to=data.to,
        cc=data.cc,
        bcc=data.bcc,
        subject=data.subject,
        body=data.body,
        source="manual",
        model="",
    )

    _ = await repository.add_version(
        session,
        draft,
        source="manual_create",
        instruction=None,
        model="",
    )

    return await _finish(session, draft)


async def update_draft(
    session: AsyncSession,
    draft: ReplyDraft,
    data: DraftUpdate,
) -> DraftDetail:
    await session.refresh(draft, with_for_update=True)

    if draft.status == "sent":
        raise HTTPException(
            status_code=409,
            detail="A sent draft cannot be edited",
        )

    changed = False

    if "to" in data.model_fields_set:
        assert data.to is not None
        if draft.to_addresses != data.to:
            draft.to_addresses = data.to
            changed = True

    if "cc" in data.model_fields_set:
        assert data.cc is not None
        if draft.cc_addresses != data.cc:
            draft.cc_addresses = data.cc
            changed = True

    if "bcc" in data.model_fields_set:
        assert data.bcc is not None
        if draft.bcc_addresses != data.bcc:
            draft.bcc_addresses = data.bcc
            changed = True

    if "subject" in data.model_fields_set:
        assert data.subject is not None
        if draft.subject != data.subject:
            draft.subject = data.subject
            changed = True

    if "body" in data.model_fields_set:
        assert data.body is not None
        if draft.body != data.body:
            draft.body = data.body
            changed = True

    if changed:
        draft.current_version += 1
        draft.gmail_sync_status = "pending"
        draft.gmail_sync_error = None

        _ = await repository.add_version(
            session,
            draft,
            source="manual_edit",
            instruction=None,
            model=draft.model,
        )

    return await _finish(session, draft)


async def get_draft(
    session: AsyncSession,
    draft: ReplyDraft,
) -> DraftDetail:
    versions = await repository.list_versions(session, draft.id)

    return _draft_detail(draft, versions)


async def list_drafts(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    thread_id: uuid.UUID | None,
    page: int,
    page_size: int,
) -> DraftPage:
    drafts, total = await repository.list_drafts(
        session,
        user_id,
        account_id=account_id,
        thread_id=thread_id,
        page=page,
        page_size=page_size,
    )

    return DraftPage(
        items=[_draft_item(draft) for draft in drafts],
        total=total,
        page=page,
        page_size=page_size,
    )


async def list_thread_drafts(
    session: AsyncSession,
    user_id: uuid.UUID,
    thread: Thread,
) -> list[DraftDetail]:
    drafts = await repository.list_thread_drafts(
        session,
        user_id,
        thread.id,
    )

    grouped = await repository.versions_by_draft(
        session,
        [draft.id for draft in drafts],
    )

    return [
        _draft_detail(draft, grouped.get(draft.id, []))
        for draft in drafts
    ]


async def delete_draft(
    session: AsyncSession,
    draft: ReplyDraft,
) -> None:
    if draft.status == "sent":
        raise HTTPException(
            status_code=409,
            detail="A sent draft cannot be deleted",
        )

    await repository.delete_draft(session, draft)
    await session.commit()