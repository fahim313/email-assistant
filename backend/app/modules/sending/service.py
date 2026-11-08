import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi import HTTPException
from google.auth.exceptions import RefreshError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions.error import NotFoundError
from app.core.security import decrypt_token, encrypt_token
from app.integrations.gmail_client import (
    build_credentials,
    credentials_expiry,
    credentials_token,
    refresh_if_needed,
)
from app.integrations.gmail_send import (
    GmailSendClient,
    GmailSendError,
    build_raw_message,
    find_placeholders,
    reply_headers,
)
from app.modules.accounts.models import ConnectedAccount
from app.modules.drafts import repository as draft_repository
from app.modules.drafts import service as draft_service
from app.modules.drafts.models import ReplyDraft
from app.modules.drafts.schemas import DraftDetail
from app.modules.emails.models import Email
from app.modules.sending import repository
from app.modules.sending.models import EmailSend
from app.modules.sending.schemas import (
    SendDetail,
    SendItem,
    SendPage,
    SendRequest,
)

logger = logging.getLogger(__name__)

PENDING_STALE_SECONDS = 180

UNCONFIRMED_DETAIL = (
    "The earlier attempt may have been delivered. "
    "Check Gmail Sent before attempting another send."
)


@dataclass(frozen=True, slots=True)
class _Outgoing:
    send_id: uuid.UUID
    user_id: uuid.UUID
    draft_id: uuid.UUID
    account_id: uuid.UUID
    sender: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body: str
    in_reply_to: str | None
    references: str | None
    thread_id: str | None
    gmail_draft_id: str | None


def _describe(exc: GmailSendError) -> str:
    if exc.status_code == 401:
        return "Gmail rejected the credentials. Reconnect the account."

    if exc.status_code == 403:
        return (
            "Gmail denied the request. Check Gmail permissions "
            "and API quota."
        )

    if exc.status_code == 429:
        return "Gmail rate limit reached. Try again later."

    return f"Gmail error: {exc}"[:300]


def _item_data(send: EmailSend) -> dict[str, object]:
    return {
        "id": send.id,
        "draft_id": send.draft_id,
        "account_id": send.account_id,
        "status": send.status,
        "to_addresses": list(send.to_addresses),
        "cc_addresses": list(send.cc_addresses),
        "bcc_addresses": list(send.bcc_addresses),
        "subject": send.subject,
        "draft_version": send.draft_version,
        "gmail_message_id": send.gmail_message_id,
        "gmail_thread_id": send.gmail_thread_id,
        "error_message": send.error_message,
        "outcome_unknown": send.outcome_unknown,
        "attempts": send.attempts,
        "sent_at": send.sent_at,
        "created_at": send.created_at,
        "updated_at": send.updated_at,
    }


def _item(send: EmailSend) -> SendItem:
    return SendItem.model_validate(_item_data(send))


def _detail(send: EmailSend) -> SendDetail:
    return SendDetail.model_validate(
        {
            **_item_data(send),
            "body": send.body,
        }
    )


async def _access_token(
    session: AsyncSession,
    account: ConnectedAccount,
) -> str:
    if account.needs_reauth:
        raise HTTPException(
            status_code=409,
            detail="Gmail account requires reconnection.",
        )

    if not account.access_token_encrypted:
        raise HTTPException(
            status_code=409,
            detail="Gmail access token is unavailable.",
        )

    scopes = (account.scopes or "").split()

    credentials = build_credentials(
        access_token=decrypt_token(account.access_token_encrypted),
        refresh_token=(
            decrypt_token(account.refresh_token_encrypted)
            if account.refresh_token_encrypted
            else None
        ),
        expires_at=account.token_expires_at,
        scopes=scopes,
    )

    try:
        refreshed = await asyncio.to_thread(
            refresh_if_needed,
            credentials,
        )

    except (ValueError, RefreshError) as exc:
        account.needs_reauth = True
        await session.commit()

        raise HTTPException(
            status_code=409,
            detail="Gmail access expired. Reconnect the account.",
        ) from exc

    token = credentials_token(credentials)

    if refreshed:
        account.access_token_encrypted = encrypt_token(token)
        account.token_expires_at = credentials_expiry(credentials)

        new_refresh_token = cast(str | None, credentials.refresh_token)

        if new_refresh_token:
            account.refresh_token_encrypted = encrypt_token(new_refresh_token)

        await session.commit()

    return token


async def _gmail_client(
    session: AsyncSession,
    account: ConnectedAccount,
) -> GmailSendClient:
    token = await _access_token(session, account)

    return GmailSendClient(token)


def _validate_draft(draft: ReplyDraft) -> None:
    if draft.status == "sent":
        raise HTTPException(
            status_code=409,
            detail="This draft was already sent.",
        )

    if not draft.to_addresses:
        raise HTTPException(
            status_code=422,
            detail="The draft has no recipients.",
        )

    if not draft.body.strip():
        raise HTTPException(
            status_code=422,
            detail="The draft body is empty.",
        )


def _ensure_no_placeholders(body: str) -> None:
    placeholders = find_placeholders(body)

    if placeholders:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Resolve the placeholders before sending.",
                "placeholders": placeholders,
            },
        )


async def _reply_context(
    session: AsyncSession,
    draft: ReplyDraft,
) -> tuple[str | None, str | None, str | None]:
    if draft.reply_to_email_id is None:
        return None, None, None

    email = await session.get(Email, draft.reply_to_email_id)

    if email is None or email.account_id != draft.account_id:
        raise HTTPException(
            status_code=409,
            detail="The original email for this reply is unavailable.",
        )

    in_reply_to, references = reply_headers(
        email.message_id_header,
        email.references_header,
    )

    return email.thread_id, in_reply_to, references


def _build_raw(
    *,
    sender: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body: str,
    in_reply_to: str | None,
    references: str | None,
) -> str:
    return build_raw_message(
        sender=sender,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body=body,
        in_reply_to=in_reply_to,
        references=references,
        require_recipient=True,
    )


async def save_to_gmail(
    session: AsyncSession,
    draft: ReplyDraft,
) -> DraftDetail:
    _validate_draft(draft)

    account = await session.get(
        ConnectedAccount,
        draft.account_id,
    )

    if account is None:
        raise NotFoundError("account", str(draft.account_id))

    thread_id, in_reply_to, references = await _reply_context(
        session,
        draft,
    )

    gmail_draft_id = draft.gmail_draft_id
    version = draft.current_version

    raw = _build_raw(
        sender=account.email,
        to=list(draft.to_addresses),
        cc=list(draft.cc_addresses),
        bcc=list(draft.bcc_addresses),
        subject=draft.subject,
        body=draft.body,
        in_reply_to=in_reply_to,
        references=references,
    )

    try:
        client = await _gmail_client(session, account)

        if gmail_draft_id:
            try:
                reference = await client.update_draft(
                    gmail_draft_id,
                    raw,
                    thread_id=thread_id,
                )

            except GmailSendError as exc:
                if exc.status_code != 404:
                    raise

                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The previous Gmail draft no longer exists. "
                        "Check Gmail before creating another draft."
                    ),
                ) from exc

        else:
            reference = await client.create_draft(
                raw,
                thread_id=thread_id,
            )

    except GmailSendError as exc:
        message = _describe(exc)

        await session.refresh(draft, with_for_update=True)
        draft.gmail_sync_status = "failed"
        draft.gmail_sync_error = message
        await session.commit()

        raise HTTPException(
            status_code=502,
            detail=message,
        ) from exc

    await session.refresh(draft, with_for_update=True)

    if draft.status == "sent":
        raise HTTPException(
            status_code=409,
            detail="The draft was sent while saving to Gmail.",
        )

    draft.gmail_draft_id = reference.draft_id

    if draft.current_version == version:
        draft.gmail_sync_status = "synced"
        draft.gmail_sync_error = None

    await session.commit()
    await session.refresh(draft)

    return await draft_service.get_draft(session, draft)


async def _claim(
    session: AsyncSession,
    user_id: uuid.UUID,
    idempotency_key: str,
    draft: ReplyDraft,
) -> EmailSend:
    _validate_draft(draft)
    _ensure_no_placeholders(draft.body)

    cutoff = datetime.now(UTC) - timedelta(seconds=PENDING_STALE_SECONDS)

    await repository.settle_stale_sends(session, draft.id, cutoff)

    if await repository.has_pending_send(session, draft.id):
        raise HTTPException(
            status_code=409,
            detail="Another send for this draft is in progress.",
        )

    thread_id, in_reply_to, references = await _reply_context(
        session,
        draft,
    )

    return await repository.create_send(
        session,
        user_id=user_id,
        draft=draft,
        idempotency_key=idempotency_key,
        in_reply_to=in_reply_to,
        references=references,
        gmail_thread_id=thread_id,
    )


def _outgoing(
    send: EmailSend,
    account: ConnectedAccount,
    draft: ReplyDraft,
) -> _Outgoing:
    return _Outgoing(
        send_id=send.id,
        user_id=send.user_id,
        draft_id=draft.id,
        account_id=send.account_id,
        sender=account.email,
        to=list(send.to_addresses),
        cc=list(send.cc_addresses),
        bcc=list(send.bcc_addresses),
        subject=send.subject,
        body=send.body,
        in_reply_to=send.in_reply_to_header,
        references=send.references_header,
        thread_id=send.gmail_thread_id,
        gmail_draft_id=draft.gmail_draft_id,
    )


async def _finish_failure(
    session: AsyncSession,
    outgoing: _Outgoing,
    message: str,
    *,
    unknown: bool,
) -> None:
    await session.rollback()

    send = await repository.get_send_for_update(
        session,
        outgoing.user_id,
        outgoing.send_id,
    )

    if send is None:
        return

    send.status = "outcome_unknown" if unknown else "failed"
    send.outcome_unknown = unknown
    send.error_message = message[:1000]

    await session.commit()


async def _deliver(
    session: AsyncSession,
    outgoing: _Outgoing,
) -> SendDetail:
    account = await session.get(
        ConnectedAccount,
        outgoing.account_id,
    )

    if account is None:
        await _finish_failure(
            session,
            outgoing,
            "The connected account no longer exists.",
            unknown=False,
        )

        raise NotFoundError("account", str(outgoing.account_id))

    try:
        raw = _build_raw(
            sender=outgoing.sender,
            to=outgoing.to,
            cc=outgoing.cc,
            bcc=outgoing.bcc,
            subject=outgoing.subject,
            body=outgoing.body,
            in_reply_to=outgoing.in_reply_to,
            references=outgoing.references,
        )

    except ValueError as exc:
        await _finish_failure(
            session,
            outgoing,
            str(exc),
            unknown=False,
        )

        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    placeholders = find_placeholders(outgoing.body)

    if placeholders:
        await _finish_failure(
            session,
            outgoing,
            "The draft contains unresolved placeholders.",
            unknown=False,
        )

        raise HTTPException(
            status_code=422,
            detail={
                "message": "Resolve the placeholders before sending.",
                "placeholders": placeholders,
            },
        )

    try:
        client = await _gmail_client(session, account)

    except Exception:
        await _finish_failure(
            session,
            outgoing,
            "Could not obtain a Gmail access token.",
            unknown=False,
        )
        raise

    try:
        sent = await client.send_message(
            raw,
            thread_id=outgoing.thread_id,
        )

    except GmailSendError as exc:
        message = _describe(exc)

        await _finish_failure(
            session,
            outgoing,
            message,
            unknown=exc.ambiguous,
        )

        raise HTTPException(
            status_code=502,
            detail=message,
        ) from exc

    send = await repository.get_send_for_update(
        session,
        outgoing.user_id,
        outgoing.send_id,
    )

    if send is None:
        raise NotFoundError("send", str(outgoing.send_id))

    sent_at = datetime.now(UTC)

    send.status = "sent"
    send.outcome_unknown = False
    send.error_message = None
    send.gmail_message_id = sent.message_id
    send.gmail_thread_id = sent.thread_id or send.gmail_thread_id
    send.sent_at = sent_at

    draft = await draft_repository.get_owned_draft_for_update(
        session,
        outgoing.user_id,
        outgoing.draft_id,
    )

    if draft is not None:
        draft.status = "sent"
        draft.sent_at = sent_at
        draft.gmail_message_id = sent.message_id

    await session.commit()

    if outgoing.gmail_draft_id:
        try:
            await client.delete_draft(outgoing.gmail_draft_id)

        except GmailSendError as exc:
            logger.warning(
                "Could not delete Gmail draft %s: %s",
                outgoing.gmail_draft_id,
                str(exc)[:300],
            )

    final = await repository.get_owned_send(
        session,
        outgoing.user_id,
        outgoing.send_id,
    )

    if final is None:
        raise NotFoundError("send", str(outgoing.send_id))

    return _detail(final)


async def send_draft(
    session: AsyncSession,
    user_id: uuid.UUID,
    idempotency_key: str,
    data: SendRequest,
) -> tuple[SendDetail, bool]:
    draft = await draft_repository.get_owned_draft_for_update(
        session,
        user_id,
        data.draft_id,
    )

    if draft is None:
        raise NotFoundError("draft", str(data.draft_id))

    existing = await repository.get_send_by_key(
        session,
        user_id,
        idempotency_key,
    )

    if existing is not None:
        if existing.draft_id != draft.id:
            raise HTTPException(
                status_code=409,
                detail="This Idempotency-Key was used for another draft.",
            )

        if existing.status == "sent":
            result = _detail(existing)
            await session.rollback()

            return result, True

        if existing.status == "outcome_unknown":
            await session.rollback()

            raise HTTPException(
                status_code=409,
                detail=UNCONFIRMED_DETAIL,
            )

        if existing.status == "pending":
            cutoff = datetime.now(UTC) - timedelta(
                seconds=PENDING_STALE_SECONDS,
            )

            if existing.updated_at < cutoff:
                existing.status = "outcome_unknown"
                existing.outcome_unknown = True
                existing.error_message = "The previous attempt did not finish."
                await session.commit()

                raise HTTPException(
                    status_code=409,
                    detail=UNCONFIRMED_DETAIL,
                )

            await session.rollback()

            raise HTTPException(
                status_code=409,
                detail="A send for this key is already in progress.",
            )

        await session.rollback()

        raise HTTPException(
            status_code=409,
            detail=(
                "This Idempotency-Key already has a failed attempt. "
                "Check its status and use a new key for a new attempt."
            ),
        )

    account = await session.get(
        ConnectedAccount,
        draft.account_id,
    )

    if account is None:
        raise NotFoundError("account", str(draft.account_id))

    _validate_draft(draft)
    _ensure_no_placeholders(draft.body)

    try:
        _ = await _gmail_client(session, account)

    except HTTPException:
        await session.rollback()
        raise

    await session.refresh(draft, with_for_update=True)
    await session.refresh(account)

    send = await _claim(
        session,
        user_id,
        idempotency_key,
        draft,
    )

    outgoing = _outgoing(send, account, draft)

    await session.commit()

    return await _deliver(session, outgoing), False


async def list_sends(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    status: str | None,
    page: int,
    page_size: int,
) -> SendPage:
    sends, total = await repository.list_sends(
        session,
        user_id,
        account_id=account_id,
        status=status,
        page=page,
        page_size=page_size,
    )

    return SendPage(
        items=[_item(send) for send in sends],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_send(send: EmailSend) -> SendDetail:
    return _detail(send)