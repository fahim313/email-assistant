import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.db.session import SessionDep
from app.modules.analysis.dependencies import OwnedThread
from app.modules.auth.dependencies import CurrentUser
from app.modules.drafts import service
from app.modules.drafts.dependencies import OwnedDraft
from app.modules.drafts.schemas import (
    DraftCreate,
    DraftDetail,
    DraftGenerateRequest,
    DraftPage,
    DraftUpdate,
)

router = APIRouter(tags=["Drafts"])


@router.post(
    "/threads/{thread_id}/drafts/generate",
    response_model=DraftDetail,
    status_code=status.HTTP_201_CREATED,
)
async def generate_draft(
    thread: OwnedThread,
    session: SessionDep,
    body: DraftGenerateRequest | None = None,
) -> DraftDetail:
    instruction = body.instruction if body is not None else None

    return await service.generate_draft(session, thread, instruction)


@router.get(
    "/threads/{thread_id}/drafts",
    response_model=list[DraftDetail],
)
async def list_thread_drafts(
    thread: OwnedThread,
    session: SessionDep,
    user: CurrentUser,
) -> list[DraftDetail]:
    return await service.list_thread_drafts(session, user.id, thread)

@router.get("/drafts", response_model=DraftPage)
async def list_drafts(
    session: SessionDep,
    user: CurrentUser,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    thread_id: Annotated[uuid.UUID | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> DraftPage:
    return await service.list_drafts(
        session,
        user.id,
        account_id=account_id,
        thread_id=thread_id,
        page=page,
        page_size=page_size,
    )


@router.get("/drafts/{draft_id}", response_model=DraftDetail)
async def get_draft(
    draft: OwnedDraft,
    session: SessionDep,
) -> DraftDetail:
    return await service.get_draft(session, draft)


@router.post(
    "/drafts",
    response_model=DraftDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_draft(
    body: DraftCreate,
    session: SessionDep,
    user: CurrentUser,
) -> DraftDetail:
    return await service.create_draft(session, user.id, body)


@router.post(
    "/drafts/{draft_id}/regenerate",
    response_model=DraftDetail,
)
async def regenerate_draft(
    draft: OwnedDraft,
    session: SessionDep,
    body: DraftGenerateRequest | None = None,
) -> DraftDetail:
    instruction = body.instruction if body is not None else None

    return await service.regenerate_draft(session, draft, instruction)


@router.patch(
    "/drafts/{draft_id}",
    response_model=DraftDetail,
)
async def update_draft(
    draft: OwnedDraft,
    body: DraftUpdate,
    session: SessionDep,
) -> DraftDetail:
    return await service.update_draft(session, draft, body)


@router.delete(
    "/drafts/{draft_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_draft(
    draft: OwnedDraft,
    session: SessionDep,
) -> Response:
    await service.delete_draft(session, draft)

    return Response(status_code=status.HTTP_204_NO_CONTENT)