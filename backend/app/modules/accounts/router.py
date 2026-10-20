import asyncio
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from google.auth.exceptions import GoogleAuthError
from oauthlib.oauth2.rfc6749.errors import OAuth2Error
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import SessionDep
from app.modules.accounts.google_oauth import (
    exchange_google_code,
    get_google_authorization_url,
    get_google_user_info,
)
from app.modules.accounts.models import OAuthState
from app.modules.accounts.service import save_google_account
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["Accounts"])


class GoogleConnectResponse(BaseModel):
    authorization_url: str


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
        # These calls are blocking (network), so run them in a thread
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
        _account = await save_google_account(
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

    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/settings/accounts?connected=true",
        status_code=status.HTTP_303_SEE_OTHER,
    )