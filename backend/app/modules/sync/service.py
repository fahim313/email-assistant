import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions.error import ReconnectRequiredError
from app.db.session import SessionLocal
from app.integrations import gmail_client as gmail
from app.integrations.gmail_client import EmailUpsertData, GmailService
from app.modules.accounts import service as accounts_service
from app.modules.accounts.models import ConnectedAccount
from app.modules.emails import repository as emails_repository
from app.modules.emails.models import Email
from app.modules.sync import repository
from app.modules.sync.models import SyncRun
from app.modules.sync.schemas import SyncStatusResponse, SyncStepStatus
from app.modules.threads import repository as threads_repository

logger = logging.getLogger(__name__)

STEPS: list[tuple[str, str]] = [
    ("connecting", "Connecting to Gmail"),
    ("inbox", "Fetching your inbox"),
    ("sent", "Reading your sent mail"),
    ("threads", "Grouping conversations"),
    ("done", "Finished"),
]
_STEP_INDEX = {key: index for index, (key, _) in enumerate(STEPS)}

FETCH_RETRIES = 4
RETRY_WAITS_SECONDS = (60, 120, 240)
NON_QUOTA_MAX_WAIT_SECONDS = 15

MAX_BATCH_SIZE = 20
BATCH_PAUSE_SECONDS = 2.0


def _msg(*sentences: str) -> str:
    """Join sentences into one user-facing message."""
    return " ".join(sentences)


def build_status(account: ConnectedAccount) -> SyncStatusResponse:
    if account.sync_status == "synced":
        progress = 100
    elif account.sync_total > 0:
        progress = min(100, int(account.sync_done / account.sync_total * 100))
    else:
        progress = 0

    current = _STEP_INDEX.get(account.sync_step or "", -1)
    steps: list[SyncStepStatus] = []

    for index, (key, label) in enumerate(STEPS):
        if account.sync_status == "synced":
            state = "done"
        elif account.sync_status in ("syncing", "failed") and current >= 0:
            if index < current:
                state = "done"
            elif index == current:
                state = (
                    "active"
                    if account.sync_status == "syncing"
                    else "failed"
                )
            else:
                state = "pending"
        else:
            state = "pending"

        steps.append(
            SyncStepStatus(key=key, label=label, state=state)
        )

    return SyncStatusResponse(
        account_id=account.id,
        status=account.sync_status,
        step=account.sync_step,
        steps=steps,
        done=account.sync_done,
        total=account.sync_total,
        progress=progress,
        error=account.sync_error,
        needs_reauth=account.needs_reauth,
        last_synced_at=account.last_synced_at,
    )


async def request_sync(
    session: AsyncSession,
    account: ConnectedAccount,
) -> bool:
    """Mark an idle account as pending."""
    if account.needs_reauth:
        raise ReconnectRequiredError(
            "This Gmail account was disconnected. Please reconnect it."
        )

    if account.sync_status == "syncing":
        return False

    account.sync_status = "pending"
    account.sync_step = None
    account.sync_error = None
    await session.commit()
    return True


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, (RefreshError, ReconnectRequiredError)):
        return _msg(
            "Google access expired or was revoked.",
            "Please reconnect this Gmail account.",
        )

    if isinstance(exc, HttpError):
        status_code = gmail.http_status(exc)

        if status_code == 401:
            return _msg(
                "Gmail access expired or was revoked.",
                "Please reconnect this Gmail account.",
            )

        if status_code == 403:
            if gmail.is_quota_error(exc):
                return _msg(
                    "Gmail API quota exceeded.",
                    "Please wait before trying the sync again.",
                )

            return _msg(
                "Gmail refused access (403).",
                "Check Gmail API permissions and access settings.",
            )

        if status_code == 429:
            return _msg(
                "Gmail is limiting requests right now.",
                "Please wait before trying again.",
            )

        return f"Gmail API error ({status_code})."

    if isinstance(exc, ValueError):
        return str(exc)[:500]

    return f"Sync failed ({type(exc).__name__})."


def _batch_size() -> int:
    return max(1, min(get_settings().SYNC_BATCH_SIZE, MAX_BATCH_SIZE))


async def _set_step(
    session: AsyncSession,
    account: ConnectedAccount,
    step: str,
) -> None:
    account.sync_step = step
    await session.commit()


async def _existing_messages(
    session: AsyncSession,
    account_id: uuid.UUID,
) -> dict[str, str]:
    """Map Gmail message IDs to stored Gmail thread IDs."""
    result = await session.execute(
        select(Email.gmail_message_id, Email.thread_id).where(
            Email.account_id == account_id
        )
    )
    return {
        message_id: thread_id
        for message_id, thread_id in result.all()
    }


async def _fetch_with_retry(
    service: GmailService,
    ids: list[str],
) -> tuple[list[EmailUpsertData], list[str]]:
    """Fetch messages and retry failed IDs."""
    messages: list[EmailUpsertData] = []
    gone: list[str] = []
    pending = ids

    for attempt in range(FETCH_RETRIES):
        try:
            got, retry, missing, quota_limited = await asyncio.to_thread(
                gmail.fetch_messages,
                service,
                pending,
            )
        except HttpError as exc:
            if gmail.http_status(exc) == 404:
                gone.extend(pending)
                return messages, gone

            quota_limited = gmail.is_quota_error(exc)
            retry = pending
            got = []
            missing = []

        messages.extend(got)
        gone.extend(missing)

        if not retry:
            return messages, gone

        pending = retry

        if attempt == FETCH_RETRIES - 1:
            break

        wait = RETRY_WAITS_SECONDS[attempt]
        if not quota_limited:
            wait = min(wait, NON_QUOTA_MAX_WAIT_SECONDS)

        logger.warning(
            "Retrying %d Gmail messages in %ds (attempt %d/%d, quota=%s)",
            len(pending),
            wait,
            attempt + 1,
            FETCH_RETRIES,
            quota_limited,
        )
        await asyncio.sleep(wait)

    raise ValueError(
        _msg(
            "Gmail is temporarily limiting requests.",
            "Please try syncing again in a few minutes.",
        )
    )


async def _store_batch(
    session: AsyncSession,
    account: ConnectedAccount,
    service: GmailService,
    ids: list[str],
) -> set[str]:
    """Fetch and persist one batch, returning affected thread IDs."""
    if not ids:
        return set()

    messages, gone = await _fetch_with_retry(service, ids)

    wanted: list[EmailUpsertData] = [
        message
        for message in messages
        if gmail.is_wanted(message["labels"])
    ]
    unwanted = [
        message["gmail_message_id"]
        for message in messages
        if not gmail.is_wanted(message["labels"])
    ]

    touched = {message["thread_id"] for message in wanted}

    touched |= await emails_repository.delete_emails(
        session,
        account.id,
        [*unwanted, *gone],
    )

    await emails_repository.upsert_emails(
        session,
        account.id,
        wanted,
    )

    return touched


async def _sync_full(
    session: AsyncSession,
    account: ConnectedAccount,
    service: GmailService,
    *,
    skip_existing: bool,
) -> tuple[set[str], int]:
    settings = get_settings()
    window = f"newer_than:{settings.SYNC_WINDOW_DAYS}d"
    batch_size = _batch_size()

    inbox_ids = await asyncio.to_thread(
        gmail.list_message_ids,
        service,
        f"in:inbox {window}",
        settings.SYNC_MAX_INBOX_MESSAGES,
    )
    sent_ids = await asyncio.to_thread(
        gmail.list_message_ids,
        service,
        f"in:sent {window}",
        settings.SYNC_MAX_SENT_MESSAGES,
    )

    inbox_set = set(inbox_ids)
    sent_ids = [
        message_id
        for message_id in sent_ids
        if message_id not in inbox_set
    ]

    existing = (
        await _existing_messages(session, account.id)
        if skip_existing
        else {}
    )

    touched = {
        existing[message_id]
        for message_id in (*inbox_ids, *sent_ids)
        if message_id in existing
    }

    account.sync_total = len(inbox_ids) + len(sent_ids)
    account.sync_done = 0
    await session.commit()

    done = 0

    for step, ids in (("inbox", inbox_ids), ("sent", sent_ids)):
        await _set_step(session, account, step)

        todo = [
            message_id
            for message_id in ids
            if message_id not in existing
        ]
        done += len(ids) - len(todo)

        account.sync_done = done
        await session.commit()

        for start in range(0, len(todo), batch_size):
            chunk = todo[start : start + batch_size]

            touched |= await _store_batch(
                session,
                account,
                service,
                chunk,
            )

            done += len(chunk)
            account.sync_done = done
            await session.commit()
            await asyncio.sleep(BATCH_PAUSE_SECONDS)

    return touched, done


async def _sync_incremental(
    session: AsyncSession,
    account: ConnectedAccount,
    service: GmailService,
    start_history_id: str,
) -> tuple[set[str], int]:
    batch_size = _batch_size()

    changes = await asyncio.to_thread(
        gmail.list_history,
        service,
        start_history_id,
    )
    ids = sorted(changes.added | changes.changed)

    account.sync_total = len(ids)
    account.sync_done = 0
    await _set_step(session, account, "inbox")

    touched = await emails_repository.delete_emails(
        session,
        account.id,
        changes.deleted,
    )

    done = 0

    for start in range(0, len(ids), batch_size):
        chunk = ids[start : start + batch_size]

        touched |= await _store_batch(
            session,
            account,
            service,
            chunk,
        )

        done += len(chunk)
        account.sync_done = done
        await session.commit()
        await asyncio.sleep(BATCH_PAUSE_SECONDS)

    await session.commit()
    return touched, done


async def _sync(
    session: AsyncSession,
    account: ConnectedAccount,
    run: SyncRun,
    *,
    full: bool,
) -> None:
    creds = await accounts_service.get_credentials(session, account)
    service = await asyncio.to_thread(gmail.build_service, creds)

    profile = await asyncio.to_thread(gmail.get_profile, service)

    use_incremental = (
        not full
        and account.history_id is not None
        and account.last_synced_at is not None
    )

    if use_incremental:
        try:
            touched, count = await _sync_incremental(
                session,
                account,
                service,
                str(account.history_id),
            )
            run.mode = "incremental"
        except gmail.HistoryExpiredError:
            logger.info(
                "History expired for %s; running full sync",
                account.id,
            )
            run.mode = "full"
            touched, count = await _sync_full(
                session,
                account,
                service,
                skip_existing=not full,
            )
    else:
        run.mode = "full"
        touched, count = await _sync_full(
            session,
            account,
            service,
            skip_existing=not full,
        )

    await _set_step(session, account, "threads")
    await threads_repository.rebuild_threads(
        session,
        account.id,
        touched,
    )

    account.history_id = profile.get("historyId")
    account.sync_status = "synced"
    account.sync_step = "done"
    account.sync_error = None
    account.last_synced_at = datetime.now(UTC)

    run.status = "success"
    run.messages_synced = count
    run.finished_at = datetime.now(UTC)

    await session.commit()


async def run_sync(account_id: uuid.UUID, *, full: bool = False) -> None:
    """Sync recent messages on a full sync and changes on incremental sync."""
    settings = get_settings()

    async with SessionLocal() as session:
        stale_after = timedelta(minutes=settings.SYNC_STALE_MINUTES)

        if not await repository.claim(session, account_id, stale_after):
            return

        account = await session.get(ConnectedAccount, account_id)
        if account is None:
            return

        run = SyncRun(
            account_id=account_id,
            mode="full" if full or not account.history_id else "incremental",
        )
        session.add(run)
        await session.commit()
        run_id = run.id

        try:
            await _sync(session, account, run, full=full)
        except Exception as exc:
            await session.rollback()

            if not isinstance(exc, ReconnectRequiredError):
                logger.exception(
                    "Gmail sync failed for account %s",
                    account_id,
                )

            message = _friendly_error(exc)
            await repository.mark_failed(
                session,
                account_id,
                message,
            )
            await repository.finish_run(
                session,
                run_id,
                "failed",
                message,
            )