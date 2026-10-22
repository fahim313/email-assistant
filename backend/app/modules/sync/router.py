import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, status

from app.db.session import SessionDep
from app.modules.accounts.service import get_owned_account
from app.modules.auth.dependencies import CurrentUser
from app.modules.sync import service
from app.modules.sync.schemas import SyncStatusResponse
from app.modules.sync.tasks import enqueue_sync

# Same URLs the frontend already uses: /accounts/{id}/sync[-status]
router = APIRouter(prefix="/accounts", tags=["Sync"])


@router.get("/{account_id}/sync-status", response_model=SyncStatusResponse)
async def get_sync_status(
    account_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
) -> SyncStatusResponse:
    account = await get_owned_account(session, account_id, user.id)
    return service.build_status(account)


@router.post(
    "/{account_id}/sync",
    response_model=SyncStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_sync(
    account_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    user: CurrentUser,
    full: Annotated[
        bool,
        Query(description="Re-download the whole 30-day window."),
    ] = False,
) -> SyncStatusResponse:
    account = await get_owned_account(session, account_id, user.id)

    if await service.request_sync(session, account):
        enqueue_sync(background_tasks, account.id, full=full)

    return service.build_status(account)
