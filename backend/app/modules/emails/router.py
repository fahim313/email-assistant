import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.db.session import SessionDep
from app.modules.auth.dependencies import CurrentUser
from app.modules.emails import service
from app.modules.emails.schemas import EmailDetail, EmailPage

router = APIRouter(prefix="/emails", tags=["Emails"])


@router.get("", response_model=EmailPage)
async def list_emails(
    session: SessionDep,
    user: CurrentUser,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    thread_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    unread_only: Annotated[bool, Query()] = False,
    is_sent: Annotated[bool | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> EmailPage:
    return await service.list_emails(
        session,
        user.id,
        account_id=account_id,
        thread_id=thread_id,
        q=q,
        unread_only=unread_only,
        is_sent=is_sent,
        page=page,
        page_size=page_size,
    )


@router.get("/{email_id}", response_model=EmailDetail)
async def get_email(
    email_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
) -> EmailDetail:
    return await service.get_email_detail(session, user.id, email_id)
