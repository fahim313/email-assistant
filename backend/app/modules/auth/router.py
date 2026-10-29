import asyncio
import logging
import secrets
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request, Response, params, status
from fastapi.responses import RedirectResponse
from google.auth.exceptions import GoogleAuthError
from oauthlib.oauth2.rfc6749.errors import OAuth2Error

from app.core.config import get_settings
from app.core.exceptions.error import AuthenticationError
from app.core.rate_limit import RateLimiter
from app.db.session import SessionDep
from app.modules.auth import service
from app.modules.auth.dependencies import CurrentUser
from app.modules.auth.google import (
    GoogleIdentity,
    exchange_login_code,
    get_login_authorization_url,
    verify_google_id_token,
)
from app.modules.auth.schemas import (
    GoogleIdTokenRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

GOOGLE_STATE_COOKIE = "google_login_state"
GOOGLE_COOKIE_PATH = "/api/v1/auth/google"

# One shared bucket for every credential-taking endpoint.
auth_rate_limit = RateLimiter(
    limit=get_settings().AUTH_RATE_LIMIT_PER_MINUTE,
    window_seconds=60,
    scope="auth",
)
RateLimited = params.Depends(dependency=auth_rate_limit)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RateLimited],
)
async def register(
    data: RegisterRequest,
    session: SessionDep,
) -> TokenResponse:
    return await service.register_user(session, data)


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[RateLimited],
)
async def login(
    data: LoginRequest,
    session: SessionDep,
) -> TokenResponse:
    return await service.login_user(session, data)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[RateLimited],
)
async def refresh(
    data: RefreshRequest,
    session: SessionDep,
) -> TokenResponse:
    return await service.refresh_session(session, data.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    data: LogoutRequest,
    session: SessionDep,
) -> Response:
    await service.logout(session, data.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def read_current_user(user: CurrentUser) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        name=user.name,
        email=user.email,
        is_active=user.is_active,
    )


def _frontend_redirect(path: str, **params: str) -> RedirectResponse:
    settings = get_settings()
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}{path}{query}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/google/login", dependencies=[RateLimited])
async def google_login() -> RedirectResponse:
    settings = get_settings()
    state = secrets.token_urlsafe(32)

    response = RedirectResponse(
        url=get_login_authorization_url(state),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    response.set_cookie(
        GOOGLE_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,  # pyright: ignore[reportArgumentType]
        path=GOOGLE_COOKIE_PATH,
    )
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    session: SessionDep,
    state: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    settings = get_settings()

    def fail(reason: str) -> RedirectResponse:
        response = _frontend_redirect("/login", error=reason)
        response.delete_cookie(GOOGLE_STATE_COOKIE, path=GOOGLE_COOKIE_PATH)
        return response

    if error or not code:
        return fail("google_cancelled")

    cookie_state = request.cookies.get(GOOGLE_STATE_COOKIE)
    if (
        not state
        or not cookie_state
        or not secrets.compare_digest(state, cookie_state)
    ):
        return fail("google_state_invalid")

    try:
        identity = await asyncio.to_thread(exchange_login_code, code)
        tokens = await service.login_with_google(session, identity)
    except (ValueError, TypeError, GoogleAuthError, OAuth2Error) as exc:
        logger.warning("Google sign-in failed: %s", type(exc).__name__)
        return fail("google_failed")
    except AuthenticationError:
        return fail("google_failed")
    except Exception as exc:
      
        logger.exception("Google sign-in error: %s", type(exc).__name__)
        return fail("google_failed")

    fragment = urlencode(
        {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "token_type": tokens.token_type,
            "expires_in": str(tokens.expires_in),
        }
    )
    response = RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/callback#{fragment}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(GOOGLE_STATE_COOKIE, path=GOOGLE_COOKIE_PATH)
    return response


@router.post(
    "/google/token",
    response_model=TokenResponse,
    dependencies=[RateLimited],
)
async def google_token(
    data: GoogleIdTokenRequest,
    session: SessionDep,
) -> TokenResponse:
    """Alternative to the redirect flow: the frontend obtains a Google ID
    token with Google Identity Services and posts it here."""
    try:
        identity: GoogleIdentity = await asyncio.to_thread(
            verify_google_id_token, data.id_token
        )
    except (ValueError, TypeError, GoogleAuthError):
        raise AuthenticationError("Invalid Google credential.") from None

    return await service.login_with_google(session, identity)