import uuid
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.accounts.models import ConnectedAccount
from app.modules.drafts.models import ReplyDraft, ReplyDraftVersion


async def get_owned_draft(
    session: AsyncSession,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> ReplyDraft | None:
    result = await session.execute(
        select(ReplyDraft)
        .join(
            ConnectedAccount,
            ReplyDraft.account_id == ConnectedAccount.id,
        )
        .where(
            ReplyDraft.id == draft_id,
            ConnectedAccount.user_id == user_id,
        )
    )

    return result.scalar_one_or_none()


async def get_owned_draft_for_update(
    session: AsyncSession,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> ReplyDraft | None:
    result = await session.execute(
        select(ReplyDraft)
        .join(
            ConnectedAccount,
            ReplyDraft.account_id == ConnectedAccount.id,
        )
        .where(
            ReplyDraft.id == draft_id,
            ConnectedAccount.user_id == user_id,
        )
        .with_for_update(of=ReplyDraft)
    )

    return result.scalar_one_or_none()


async def list_drafts(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    thread_id: uuid.UUID | None,
    page: int,
    page_size: int,
) -> tuple[list[ReplyDraft], int]:
    conditions: list[ColumnElement[bool]] = [
        ConnectedAccount.user_id == user_id,
    ]

    if account_id is not None:
        conditions.append(ReplyDraft.account_id == account_id)

    if thread_id is not None:
        conditions.append(ReplyDraft.thread_id == thread_id)

    total = await session.scalar(
        select(func.count(ReplyDraft.id))
        .select_from(ReplyDraft)
        .join(
            ConnectedAccount,
            ReplyDraft.account_id == ConnectedAccount.id,
        )
        .where(*conditions)
    )

    result = await session.execute(
        select(ReplyDraft)
        .join(
            ConnectedAccount,
            ReplyDraft.account_id == ConnectedAccount.id,
        )
        .where(*conditions)
        .order_by(
            ReplyDraft.updated_at.desc(),
            ReplyDraft.id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    return list(result.scalars().all()), total or 0


async def list_thread_drafts(
    session: AsyncSession,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> list[ReplyDraft]:
    result = await session.execute(
        select(ReplyDraft)
        .join(
            ConnectedAccount,
            ReplyDraft.account_id == ConnectedAccount.id,
        )
        .where(
            ReplyDraft.thread_id == thread_id,
            ConnectedAccount.user_id == user_id,
        )
        .order_by(
            ReplyDraft.updated_at.desc(),
            ReplyDraft.id,
        )
    )

    return list(result.scalars().all())


async def list_versions(
    session: AsyncSession,
    draft_id: uuid.UUID,
) -> list[ReplyDraftVersion]:
    result = await session.execute(
        select(ReplyDraftVersion)
        .where(ReplyDraftVersion.draft_id == draft_id)
        .order_by(ReplyDraftVersion.version_number.desc())
    )

    return list(result.scalars().all())


async def versions_by_draft(
    session: AsyncSession,
    draft_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[ReplyDraftVersion]]:
    grouped: defaultdict[
        uuid.UUID,
        list[ReplyDraftVersion],
    ] = defaultdict(list)

    if not draft_ids:
        return {}

    result = await session.execute(
        select(ReplyDraftVersion)
        .where(ReplyDraftVersion.draft_id.in_(draft_ids))
        .order_by(ReplyDraftVersion.version_number.desc())
    )

    for version in result.scalars().all():
        grouped[version.draft_id].append(version)

    return dict(grouped)


async def create_draft(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    thread_id: uuid.UUID | None,
    reply_to_email_id: uuid.UUID | None,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body: str,
    source: str,
    model: str,
) -> ReplyDraft:
    draft = ReplyDraft(
        account_id=account_id,
        thread_id=thread_id,
        reply_to_email_id=reply_to_email_id,
        to_addresses=to,
        cc_addresses=cc,
        bcc_addresses=bcc,
        subject=subject,
        body=body,
        status="draft",
        source=source,
        current_version=1,
        model=model,
        gmail_sync_status="pending",
    )

    session.add(draft)
    await session.flush()

    return draft


async def add_version(
    session: AsyncSession,
    draft: ReplyDraft,
    *,
    source: str,
    instruction: str | None,
    model: str,
) -> ReplyDraftVersion:
    version = ReplyDraftVersion(
        draft_id=draft.id,
        version_number=draft.current_version,
        source=source,
        instruction=instruction,
        model=model,
        subject=draft.subject,
        body=draft.body,
        to_addresses=list(draft.to_addresses),
        cc_addresses=list(draft.cc_addresses),
        bcc_addresses=list(draft.bcc_addresses),
    )

    session.add(version)
    await session.flush()

    return version


async def delete_draft(
    session: AsyncSession,
    draft: ReplyDraft,
) -> None:
    await session.delete(draft)
    await session.flush()