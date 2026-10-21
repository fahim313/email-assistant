import asyncio
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    status,
)
from fastapi.responses import RedirectResponse
from google.auth.exceptions import GoogleAuthError
from oauthlib.oauth2.rfc6749.errors import OAuth2Error
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import SessionDep
from app.modules.accounts import repository
from app.modules.accounts.google_oauth import (
    exchange_google_code,
    get_google_authorization_url,
    get_google_user_info,
)
from app.modules.accounts.models import ConnectedAccount, OAuthState
from app.modules.accounts.schemas import (
    AccountResponse,
    GoogleConnectResponse,
    SyncStatusResponse,
)
from app.modules.accounts.service import save_google_account
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.sync.service import run_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["Accounts"])


def _sync_status(account: ConnectedAccount) -> SyncStatusResponse:
    if account.sync_status == "synced":
        progress = 100
    elif account.sync_total > 0:
        progress = min(100, int(account.sync_done / account.sync_total * 100))
    else:
        progress = 0

    return SyncStatusResponse(
        account_id=account.id,
        status=account.sync_status,
        done=account.sync_done,
        total=account.sync_total,
        progress=progress,
        error=account.sync_error,
        last_synced_at=account.last_synced_at,
    )


async def _get_owned_account(
    session: SessionDep,
    account_id: uuid.UUID,
    user: User,
) -> ConnectedAccount:
    account = await repository.get_by_id(session, account_id, user.id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connected account not found.",
        )
    return account


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    session: SessionDep,
    user: Annotated[User, Depends(get_current_user)],
) -> list[AccountResponse]:
    accounts = await repository.list_by_user(session, user.id)
    return [AccountResponse.model_validate(a) for a in accounts]


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_account(
    account_id: uuid.UUID,
    session: SessionDep,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    account = await _get_owned_account(session, account_id, user)
    try:
        # emails are removed by ON DELETE CASCADE
        await session.delete(account)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not disconnect the account.",
        ) from None


@router.get("/{account_id}/sync-status", response_model=SyncStatusResponse)
async def get_sync_status(
    account_id: uuid.UUID,
    session: SessionDep,
    user: Annotated[User, Depends(get_current_user)],
) -> SyncStatusResponse:
    account = await _get_owned_account(session, account_id, user)
    return _sync_status(account)


@router.post(
    "/{account_id}/sync",
    response_model=SyncStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_sync(
    account_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    user: Annotated[User, Depends(get_current_user)],
) -> SyncStatusResponse:
    account = await _get_owned_account(session, account_id, user)

    if account.sync_status != "syncing":
        account.sync_status = "pending"
        account.sync_error = None
        await session.commit()
        background_tasks.add_task(run_sync, account.id)

    return _sync_status(account)


@router.get("/google/connect", response_model=GoogleConnectResponse)
async def connect_google_account(
    session: SessionDep,
    user: Annotated[User, Depends(get_current_user)],
) -> GoogleConnectResponse:
    state = secrets.token_urlsafe(32)

    oauth_state = OAuthState(
        state=state,
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    try:
        session.add(oauth_state)
        await session.commit()
        authorization_url = get_google_authorization_url(state)
    except (SQLAlchemyError, ValueError, GoogleAuthError, OAuth2Error):
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start Google account authorization.",
        ) from None

    return GoogleConnectResponse(authorization_url=authorization_url)


@router.get("/google/callback")
async def google_callback(
    background_tasks: BackgroundTasks,
    session: SessionDep,
    state: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    settings = get_settings()

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account authorization was not completed.",
        )

    if not state or not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state or authorization code is missing.",
        )

    try:
        result = await session.execute(
            delete(OAuthState)
            .where(
                OAuthState.state == state,
                OAuthState.expires_at > datetime.now(UTC),
            )
            .returning(OAuthState.user_id)
        )
        user_id = result.scalar_one_or_none()

        if user_id is None:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth state is invalid, expired, or already used.",
            )

        await session.commit()

    except SQLAlchemyError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not validate Google authorization.",
        ) from None

    try:
        flow = await asyncio.to_thread(exchange_google_code, code, state)
        google_user = await asyncio.to_thread(get_google_user_info, flow)
        credentials = flow.credentials

        access_token = getattr(credentials, "token", None)
        refresh_token = getattr(credentials, "refresh_token", None)
        raw_expiry = getattr(credentials, "expiry", None)
        raw_scopes = getattr(credentials, "scopes", None)

        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Google access token is missing.")

        if refresh_token is not None and not isinstance(refresh_token, str):
            raise ValueError("Google refresh token is invalid.")

        expires_at: datetime | None = None

        if isinstance(raw_expiry, datetime):
            expires_at = raw_expiry
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)

        scopes: list[str] = []

        if isinstance(raw_scopes, (list, tuple)):
            scopes = [
                cast(str, scope)
                for scope in raw_scopes
                if isinstance(scope, str)
            ]

    except (ValueError, TypeError, GoogleAuthError, OAuth2Error) as exc:
        logger.exception(
            "Google OAuth verification failed: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not verify Google authorization. Please try again.",
        ) from None

    try:
        account = await save_google_account(
            session=session,
            user_id=user_id,
            google_sub=google_user["sub"],
            email=google_user["email"],
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scopes=scopes,
        )
    except SQLAlchemyError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save the connected Google account.",
        ) from None

    # Start syncing in the background, then send the user to the sync page
    background_tasks.add_task(run_sync, account.id)

    return RedirectResponse(
        url=(
            f"{settings.FRONTEND_URL}/onboarding/sync"
            f"?account_id={account.id}"
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )
