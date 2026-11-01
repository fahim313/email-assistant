from typing import Annotated

from fastapi import APIRouter, Query

from app.db.session import SessionDep
from app.modules.analysis import service
from app.modules.analysis.dependencies import (
    OwnedAccount,
    OwnedActionItem,
    OwnedThread,
)
from app.modules.analysis.schemas import (
    ActionItemItem,
    ActionItemUpdate,
    AnalysisItem,
    AnalysisPreview,
    AnalysisStatus,
    BatchStarted,
)

router = APIRouter(tags=["Analysis"])


@router.post("/threads/{thread_id}/analyze", response_model=AnalysisItem)
async def analyze_thread(
    thread: OwnedThread,
    session: SessionDep,
    force: Annotated[bool, Query()] = False,
) -> AnalysisItem:
    return await service.analyze_thread(session, thread, force=force)


@router.get("/threads/{thread_id}/analysis", response_model=AnalysisItem)
async def get_thread_analysis(
    thread: OwnedThread,
    session: SessionDep,
) -> AnalysisItem:
    return await service.get_thread_analysis(session, thread)


@router.get(
    "/threads/{thread_id}/analysis-preview",
    response_model=AnalysisPreview,
)
async def analysis_preview(
    thread: OwnedThread,
    session: SessionDep,
) -> AnalysisPreview:
    return await service.preview(session, thread)


@router.post(
    "/accounts/{account_id}/analyze",
    response_model=BatchStarted,
    status_code=202,
)
async def analyze_account(
    account: OwnedAccount,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> BatchStarted:
    return await service.start_batch(session, account, limit)


@router.get(
    "/accounts/{account_id}/analysis-status",
    response_model=AnalysisStatus,
)
async def analysis_status(
    account: OwnedAccount,
    session: SessionDep,
) -> AnalysisStatus:
    return await service.get_status(session, account)


@router.patch("/action-items/{item_id}", response_model=ActionItemItem)
async def update_action_item(
    item: OwnedActionItem,
    body: ActionItemUpdate,
    session: SessionDep,
) -> ActionItemItem:
    return await service.update_action_item(session, item, body.done)