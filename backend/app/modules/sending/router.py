import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.db.session import SessionDep
from app.modules.auth.dependencies import CurrentUser
from app.modules.drafts.dependencies import OwnedDraft
from app.modules.drafts.schemas import DraftDetail
from app.modules.sending import service
from app.modules.sending.dependencies import IdempotencyKey, OwnedSend
from app.modules.sending.schemas import (
    SendDetail,
    SendPage,
    SendRequest,
    SendStatus,
)

router = APIRouter(tags=["Sending"])


@router.post(
    "/drafts/{draft_id}/save-to-gmail",
    response_model=DraftDetail,
)
async def save_to_gmail(
    draft: OwnedDraft,
    session: SessionDep,
) -> DraftDetail:
    return await service.save_to_gmail(session, draft)


@router.post(
    "/sending",
    response_model=SendDetail,
    status_code=status.HTTP_201_CREATED,
)
async def send_draft(
    body: SendRequest,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> SendDetail:
    detail, replayed = await service.send_draft(
        session=session,
        user_id=user.id,
        idempotency_key=idempotency_key,
        data=body,
    )

    if replayed:
        response.status_code = status.HTTP_200_OK

    return detail


@router.get("/sending", response_model=SendPage)
async def list_sends(
    session: SessionDep,
    user: CurrentUser,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    send_status: Annotated[
        SendStatus | None,
        Query(alias="status"),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> SendPage:
    return await service.list_sends(
        session,
        user.id,
        account_id=account_id,
        status=send_status,
        page=page,
        page_size=page_size,
    )


@router.get("/sending/{send_id}", response_model=SendDetail)
async def get_send(send: OwnedSend) -> SendDetail:
    return service.get_send(send)