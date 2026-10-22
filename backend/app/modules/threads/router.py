import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.db.session import SessionDep
from app.modules.auth.dependencies import CurrentUser
from app.modules.threads import service
from app.modules.threads.schemas import ThreadDetail, ThreadPage

router = APIRouter(prefix="/threads", tags=["Threads"])


@router.get("", response_model=ThreadPage)
async def list_threads(
    session: SessionDep,
    user: CurrentUser,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    unread_only: Annotated[bool, Query()] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ThreadPage:
    return await service.list_threads(
        session,
        user.id,
        account_id=account_id,
        q=q,
        unread_only=unread_only,
        page=page,
        page_size=page_size,
    )


@router.get("/{thread_id}", response_model=ThreadDetail)
async def get_thread(
    thread_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
) -> ThreadDetail:
    return await service.get_thread_detail(session, user.id, thread_id)
