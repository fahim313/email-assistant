import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app.db.session import SessionDep
from app.modules.accounts.models import ConnectedAccount
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.emails.models import Email
from app.modules.emails.schemas import EmailItem, EmailPage

router = APIRouter(prefix="/emails", tags=["Emails"])


def _to_item(email: Email, account_email: str) -> EmailItem:
    return EmailItem(
        id=email.id,
        account_id=email.account_id,
        account_email=account_email,
        thread_id=email.thread_id,
        subject=email.subject,
        sender_name=email.sender_name,
        sender_email=email.sender_email,
        snippet=email.snippet,
        received_at=email.received_at,
        labels=list(email.labels),
        is_read=email.is_read,
    )


@router.get("", response_model=EmailPage)
async def list_emails(
    session: SessionDep,
    user: Annotated[User, Depends(get_current_user)],
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> EmailPage:
    # Always scoped to the logged-in user's own accounts
    conditions: list[Any] = [ConnectedAccount.user_id == user.id]

    if account_id is not None:
        conditions.append(Email.account_id == account_id)

    if q:
        like = f"%{q}%"
        conditions.append(
            or_(
                Email.subject.ilike(like),
                Email.sender_name.ilike(like),
                Email.sender_email.ilike(like),
                Email.snippet.ilike(like),
            )
        )

    total = await session.scalar(
        select(func.count(Email.id))
        .select_from(Email)
        .join(ConnectedAccount, Email.account_id == ConnectedAccount.id)
        .where(*conditions)
    )

    result = await session.execute(
        select(Email, ConnectedAccount.email.label("account_email"))
        .join(ConnectedAccount, Email.account_id == ConnectedAccount.id)
        .where(*conditions)
        .order_by(Email.received_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items = [_to_item(row.Email, row.account_email) for row in result.all()]

    return EmailPage(
        items=items,
        total=total or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/{email_id}", response_model=EmailItem)
async def get_email(
    email_id: uuid.UUID,
    session: SessionDep,
    user: Annotated[User, Depends(get_current_user)],
) -> EmailItem:
    result = await session.execute(
        select(Email, ConnectedAccount.email.label("account_email"))
        .join(ConnectedAccount, Email.account_id == ConnectedAccount.id)
        .where(Email.id == email_id, ConnectedAccount.user_id == user.id)
    )
    row = result.first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email not found.",
        )

    return _to_item(row.Email, row.account_email)
