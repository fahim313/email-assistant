import uuid
from datetime import datetime
from typing import TypedDict

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.accounts.models import ConnectedAccount
from app.modules.analysis.models import ActionItem, ThreadAnalysis
from app.modules.analysis.schemas import AnalysisOutput
from app.modules.threads.models import Thread

MAX_FAILED_ATTEMPTS = 3


class AnalysisFields(TypedDict):
    status: str
    category: str
    priority: str
    summary: str
    is_spam: bool
    is_suspicious: bool
    risk_reason: str | None
    needs_reply: bool
    follow_up: bool
    has_deadline: bool
    deadline_at: datetime | None
    content_hash: str
    model: str
    error: str | None
    attempts: int
    analyzed_at: datetime


def _pending_condition() -> ColumnElement[bool]:
    return or_(
        ThreadAnalysis.id.is_(None),
        ThreadAnalysis.status == "pending",
        and_(
            ThreadAnalysis.status == "failed",
            ThreadAnalysis.attempts < MAX_FAILED_ATTEMPTS,
        ),
        Thread.last_message_at > ThreadAnalysis.analyzed_at,
    )


async def get_owned_thread(
    session: AsyncSession,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> Thread | None:
    result = await session.execute(
        select(Thread)
        .join(ConnectedAccount, Thread.account_id == ConnectedAccount.id)
        .where(
            Thread.id == thread_id,
            ConnectedAccount.user_id == user_id,
        )
    )

    return result.scalar_one_or_none()


async def get_owned_account(
    session: AsyncSession,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
) -> ConnectedAccount | None:
    result = await session.execute(
        select(ConnectedAccount).where(
            ConnectedAccount.id == account_id,
            ConnectedAccount.user_id == user_id,
        )
    )

    return result.scalar_one_or_none()


async def get_owned_action_item(
    session: AsyncSession,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
) -> ActionItem | None:
    result = await session.execute(
        select(ActionItem)
        .join(ThreadAnalysis, ThreadAnalysis.id == ActionItem.analysis_id)
        .join(Thread, Thread.id == ThreadAnalysis.thread_id)
        .join(ConnectedAccount, ConnectedAccount.id == Thread.account_id)
        .where(
            ActionItem.id == item_id,
            ConnectedAccount.user_id == user_id,
        )
    )

    return result.scalar_one_or_none()


async def get_analysis(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> ThreadAnalysis | None:
    result = await session.execute(
        select(ThreadAnalysis).where(ThreadAnalysis.thread_id == thread_id)
    )

    return result.scalar_one_or_none()


async def save_success(
    session: AsyncSession,
    thread_id: uuid.UUID,
    output: AnalysisOutput,
    *,
    content_hash: str,
    model: str,
    analyzed_at: datetime,
) -> ThreadAnalysis:
    analysis = await get_analysis(session, thread_id)

    done_texts: set[str] = set()

    if analysis is not None:
        action_items: list[ActionItem] = analysis.action_items

        for action_item in action_items:
            if action_item.done:
                done_texts.add(action_item.text)

    fields: AnalysisFields = {
        "status": "done",
        "category": output.category,
        "priority": output.priority,
        "summary": output.summary,
        "is_spam": output.is_spam,
        "is_suspicious": output.is_suspicious,
        "risk_reason": output.risk_reason,
        "needs_reply": output.needs_reply,
        "follow_up": output.follow_up,
        "has_deadline": output.has_deadline,
        "deadline_at": output.deadline_at,
        "content_hash": content_hash,
        "model": model,
        "error": None,
        "attempts": 0,
        "analyzed_at": analyzed_at,
    }

    if analysis is None:
        analysis = ThreadAnalysis(
            thread_id=thread_id,
            **fields,
        )
        session.add(analysis)
    else:
        analysis.status = fields["status"]
        analysis.category = fields["category"]
        analysis.priority = fields["priority"]
        analysis.summary = fields["summary"]
        analysis.is_spam = fields["is_spam"]
        analysis.is_suspicious = fields["is_suspicious"]
        analysis.risk_reason = fields["risk_reason"]
        analysis.needs_reply = fields["needs_reply"]
        analysis.follow_up = fields["follow_up"]
        analysis.has_deadline = fields["has_deadline"]
        analysis.deadline_at = fields["deadline_at"]
        analysis.content_hash = fields["content_hash"]
        analysis.model = fields["model"]
        analysis.error = fields["error"]
        analysis.attempts = fields["attempts"]
        analysis.analyzed_at = fields["analyzed_at"]

    analysis.action_items = [
        ActionItem(
            text=item.text,
            due_date=item.due_date,
            done=item.text in done_texts,
            position=index,
        )
        for index, item in enumerate(output.action_items)
    ]

    await session.flush()

    return analysis


async def save_failure(
    session: AsyncSession,
    thread_id: uuid.UUID,
    error: str,
    *,
    content_hash: str,
    model: str,
    analyzed_at: datetime,
) -> ThreadAnalysis:
    analysis = await get_analysis(session, thread_id)

    if analysis is None:
        analysis = ThreadAnalysis(
            thread_id=thread_id,
            status="failed",
            error=error[:1000],
            content_hash=content_hash,
            model=model,
            attempts=1,
            analyzed_at=analyzed_at,
        )
        session.add(analysis)
    else:
        if analysis.content_hash != content_hash:
            analysis.attempts = 0

        analysis.status = "failed"
        analysis.error = error[:1000]
        analysis.content_hash = content_hash
        analysis.model = model
        analysis.attempts += 1
        analysis.analyzed_at = analyzed_at

    await session.flush()

    return analysis


async def pending_thread_ids(
    session: AsyncSession,
    *,
    account_id: uuid.UUID | None,
    limit: int,
) -> list[uuid.UUID]:
    conditions: list[ColumnElement[bool]] = [
        Thread.has_inbox.is_(True),
        _pending_condition(),
    ]

    if account_id is not None:
        conditions.append(Thread.account_id == account_id)

    result = await session.execute(
        select(Thread.id)
        .select_from(Thread)
        .outerjoin(
            ThreadAnalysis,
            ThreadAnalysis.thread_id == Thread.id,
        )
        .where(*conditions)
        .order_by(Thread.last_message_at.desc())
        .limit(limit)
    )

    return list(result.scalars().all())


async def status_counts(
    session: AsyncSession,
    account_id: uuid.UUID,
) -> tuple[int, int, int, int]:
    total = await session.scalar(
        select(func.count(Thread.id)).where(
            Thread.account_id == account_id,
            Thread.has_inbox.is_(True),
        )
    )

    grouped = await session.execute(
        select(
            ThreadAnalysis.status,
            func.count(ThreadAnalysis.id),
        )
        .join(
            Thread,
            Thread.id == ThreadAnalysis.thread_id,
        )
        .where(
            Thread.account_id == account_id,
            Thread.has_inbox.is_(True),
        )
        .group_by(ThreadAnalysis.status)
    )

    counts = {
        status: count
        for status, count in grouped.all()
    }

    pending = await session.scalar(
        select(func.count(Thread.id))
        .select_from(Thread)
        .outerjoin(
            ThreadAnalysis,
            ThreadAnalysis.thread_id == Thread.id,
        )
        .where(
            Thread.account_id == account_id,
            Thread.has_inbox.is_(True),
            _pending_condition(),
        )
    )

    return (
        total or 0,
        counts.get("done", 0),
        counts.get("failed", 0),
        pending or 0,
    )