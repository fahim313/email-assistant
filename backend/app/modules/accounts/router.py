import asyncio
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Query,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from google.auth.exceptions import GoogleAuthError
from oauthlib.oauth2.rfc6749.errors import OAuth2Error
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.exceptions.error import AppError
from app.db.session import SessionDep
from app.modules.accounts import repository, service
from app.modules.accounts.google_oauth import (
    exchange_google_code,
    extract_google_tokens,
    get_google_authorization_url,
    get_google_user_info,
    missing_gmail_scopes,
)
from app.modules.accounts.models import OAuthState
from app.modules.accounts.schemas import (
    AccountResponse,
    GoogleConnectResponse,
)
from app.modules.auth.dependencies import CurrentUser
from app.modules.sync.tasks import enqueue_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["Accounts"])

OAUTH_STATE_TTL = timedelta(minutes=10)


def _connect_error(code: str) -> RedirectResponse:
    """Send the browser back to the app with a machine-readable error."""
    settings = get_settings()
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/onboarding/connect?{urlencode({'error': code})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    session: SessionDep,
    user: CurrentUser,
) -> list[AccountResponse]:
    accounts = await repository.list_by_user(session, user.id)
    return [AccountResponse.model_validate(a) for a in accounts]


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_account(
    account_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
) -> Response:
    account = await service.get_owned_account(session, account_id, user.id)
    try:
        await service.disconnect_account(session, account)
    except SQLAlchemyError:
        raise AppError("Could not disconnect the account.") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/google/connect", response_model=GoogleConnectResponse)
async def connect_google_account(
    session: SessionDep,
    user: CurrentUser,
) -> GoogleConnectResponse:
    state = secrets.token_urlsafe(32)
    now = datetime.now(UTC)

    try:
        # Housekeeping: forget states that were never completed.
        _ = await session.execute(delete(OAuthState).where(OAuthState.expires_at < now))
        session.add(
            OAuthState(
                state=state,
                user_id=user.id,
                expires_at=now + OAUTH_STATE_TTL,
            )
        )
        await session.commit()
        authorization_url = get_google_authorization_url(state)
    except (SQLAlchemyError, ValueError, GoogleAuthError, OAuth2Error):
        await session.rollback()
        raise AppError("Could not start Google account authorization.") from None

    return GoogleConnectResponse(authorization_url=authorization_url)


@router.get("/google/callback")
async def google_callback(
    background_tasks: BackgroundTasks,
    session: SessionDep,
    state: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Google redirects the *browser* here, so every failure is a redirect
    back to the frontend (with ?error=...), never a JSON error page."""
    settings = get_settings()

    if error:
        return _connect_error("access_denied")

    if not state or not code:
        return _connect_error("invalid_request")

    # The state is single-use: delete it and read the owner atomically.
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
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("Could not validate OAuth state")
        return _connect_error("server_error")

    if user_id is None:
        return _connect_error("invalid_state")

    try:
        flow = await asyncio.to_thread(exchange_google_code, code, state)
        google_user = await asyncio.to_thread(get_google_user_info, flow)
        tokens = extract_google_tokens(flow.credentials)
    except (ValueError, TypeError, GoogleAuthError, OAuth2Error) as exc:
        logger.warning("Google OAuth verification failed: %s", type(exc).__name__)
        return _connect_error("verification_failed")

    # The consent screen lets people untick individual Gmail permissions.
    # Without all three the app can't read, draft or send, so refuse.
    if missing_gmail_scopes(tokens.scopes):
        return _connect_error("missing_permissions")

    try:
        existing = await repository.get_by_google_sub(
            session, user_id, google_user["sub"]
        )
        if existing is None and not tokens.refresh_token:
            # Without a refresh token the account would die within an hour.
            return _connect_error("missing_refresh_token")

        account, created = await service.save_google_account(
            session=session,
            user_id=user_id,
            google_sub=google_user["sub"],
            email=google_user["email"].lower(),
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            scopes=tokens.scopes,
        )
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("Could not save connected Google account")
        return _connect_error("server_error")

    # Start syncing in the background, then send the user to the sync page.
    enqueue_sync(background_tasks, account.id)

    params = {"account_id": str(account.id)}
    if not created:
        params["already_connected"] = "1"

    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/onboarding/sync?{urlencode(params)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
