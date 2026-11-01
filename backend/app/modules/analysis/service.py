import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions.error import NotFoundError
from app.db.session import SessionLocal
from app.modules.accounts.models import ConnectedAccount
from app.modules.ai.prompts.email_analysis import (
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.modules.analysis import cleaning, repository
from app.modules.analysis.analysis_graph import AnalysisError, run_analysis
from app.modules.analysis.models import ActionItem, ThreadAnalysis
from app.modules.analysis.schemas import (
    ActionItemItem,
    AnalysisItem,
    AnalysisPreview,
    AnalysisStatus,
    BatchStarted,
)
from app.modules.threads import repository as thread_repository
from app.modules.threads.models import Thread

logger = logging.getLogger(__name__)

_running_batches: set[uuid.UUID | None] = set()
_background_tasks: set[asyncio.Task[None]] = set()
_in_flight: set[uuid.UUID] = set()


async def _prepare(
    session: AsyncSession,
    thread: Thread,
) -> cleaning.CleanedThread:
    messages = await thread_repository.list_thread_messages(
        session,
        thread,
    )

    return cleaning.build_thread_text(
        thread.subject,
        messages,
        get_settings().ANALYSIS_MAX_INPUT_CHARS,
    )


async def _analyze(
    session: AsyncSession,
    thread: Thread,
    *,
    force: bool,
) -> ThreadAnalysis:
    cleaned = await _prepare(session, thread)

    existing = await repository.get_analysis(
        session,
        thread.id,
    )

    if (
        not force
        and existing is not None
        and existing.status == "done"
        and existing.content_hash == cleaned.content_hash
    ):
        return existing

    settings = get_settings()
    model = settings.LLM_MODEL
    analyzed_at = datetime.now(UTC)

    try:
        output = await run_analysis(cleaned.text)

    except AnalysisError as exc:
        analysis = await repository.save_failure(
            session,
            thread.id,
            str(exc),
            content_hash=cleaned.content_hash,
            model=model,
            analyzed_at=analyzed_at,
        )

    else:
        analysis = await repository.save_success(
            session,
            thread.id,
            output,
            content_hash=cleaned.content_hash,
            model=model,
            analyzed_at=analyzed_at,
        )

    await session.commit()
    await session.refresh(analysis)

    return analysis


async def analyze_thread(
    session: AsyncSession,
    thread: Thread,
    *,
    force: bool = False,
) -> AnalysisItem:
    analysis = await _analyze(
        session,
        thread,
        force=force,
    )

    return AnalysisItem.model_validate(analysis)


async def get_thread_analysis(
    session: AsyncSession,
    thread: Thread,
) -> AnalysisItem:
    analysis = await repository.get_analysis(
        session,
        thread.id,
    )

    if analysis is None:
        raise NotFoundError("analysis", str(thread.id))

    return AnalysisItem.model_validate(analysis)


async def get_status(
    session: AsyncSession,
    account: ConnectedAccount,
) -> AnalysisStatus:
    total, analyzed, failed, pending = await repository.status_counts(
        session,
        account.id,
    )

    return AnalysisStatus(
        account_id=account.id,
        total=total,
        analyzed=analyzed,
        failed=failed,
        pending=pending,
    )


async def update_action_item(
    session: AsyncSession,
    item: ActionItem,
    done: bool,
) -> ActionItemItem:
    item.done = done

    await session.commit()
    await session.refresh(item)

    return ActionItemItem.model_validate(item)


async def preview(
    session: AsyncSession,
    thread: Thread,
) -> AnalysisPreview:
    settings = get_settings()

    if not settings.ENABLE_DEBUG_ENDPOINTS:
        raise NotFoundError("endpoint", "analysis-preview")

    cleaned = await _prepare(session, thread)
    now = datetime.now(UTC)

    return AnalysisPreview(
        thread_id=thread.id,
        char_count=len(cleaned.text),
        content_hash=cleaned.content_hash,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_user_prompt(
            cleaned.text,
            now,
            settings.APP_TIMEZONE,
        ),
    )


async def start_batch(
    session: AsyncSession,
    account: ConnectedAccount,
    limit: int,
) -> BatchStarted:
    account_id = account.id

    if account_id in _running_batches:
        return BatchStarted(
            account_id=account_id,
            queued=0,
            started=False,
        )

    thread_ids = await repository.pending_thread_ids(
        session,
        account_id=account_id,
        limit=limit,
    )

    if not thread_ids:
        return BatchStarted(
            account_id=account_id,
            queued=0,
            started=False,
        )

    # Reserve the account before scheduling the task.
    _running_batches.add(account_id)

    try:
        task = asyncio.create_task(
            run_batch(account_id, limit)
        )
    except Exception:
        _running_batches.discard(account_id)
        raise

    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return BatchStarted(
        account_id=account_id,
        queued=len(thread_ids),
        started=True,
    )


async def run_batch(
    account_id: uuid.UUID | None,
    limit: int,
) -> None:
    settings = get_settings()
    semaphore = asyncio.Semaphore(
        settings.ANALYSIS_CONCURRENCY
    )

    async def work(thread_id: uuid.UUID) -> None:
        if thread_id in _in_flight:
            return

        _in_flight.add(thread_id)

        try:
            async with semaphore, SessionLocal() as session:
                try:
                    thread = await session.get(
                        Thread,
                        thread_id,
                    )

                    if thread is not None:
                        _ = await _analyze(
                            session,
                            thread,
                            force=False,
                        )

                except Exception:
                    await session.rollback()
                    raise

            await asyncio.sleep(
                settings.ANALYSIS_PAUSE_SECONDS
            )

        except Exception:
            logger.exception(
                "Thread analysis failed: thread_id=%s",
                thread_id,
            )

        finally:
            _in_flight.discard(thread_id)

    try:
        async with SessionLocal() as session:
            thread_ids = await repository.pending_thread_ids(
                session,
                account_id=account_id,
                limit=limit,
            )

        _ = await asyncio.gather(
            *(work(thread_id) for thread_id in thread_ids)
        )

    except Exception:
        logger.exception(
            "Analysis batch failed: account_id=%s",
            account_id,
        )

    finally:
        _running_batches.discard(account_id)