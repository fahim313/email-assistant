from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import httpx
from google.oauth2.credentials import Credentials

from app.core.config import get_settings
from app.integrations.google_client import (
    OAuthFlow,
    create_flow,
    verify_id_token,
)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]

GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    *GMAIL_SCOPES,
]

REVOKE_URI = "https://oauth2.googleapis.com/revoke"


@dataclass(frozen=True)
class GoogleTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: list[str]


def missing_gmail_scopes(granted: list[str]) -> list[str]:
    return [scope for scope in GMAIL_SCOPES if scope not in set(granted)]


def create_google_flow(state: str | None = None) -> OAuthFlow:
    settings = get_settings()

    return create_flow(
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
        redirect_uri=settings.GOOGLE_GMAIL_REDIRECT_URI,
        scopes=GOOGLE_SCOPES,
        state=state,
    )


def get_google_authorization_url(state: str) -> str:
    flow = create_google_flow(state=state)

    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        # select_account lets the user pick a different Gmail each time;
        # consent makes Google return a refresh token every time.
        prompt="select_account consent",
    )

    return authorization_url


def exchange_google_code(code: str, state: str) -> OAuthFlow:
    flow = create_google_flow(state=state)
    flow.fetch_token(code=code)
    return flow


def extract_google_tokens(credentials: Credentials) -> GoogleTokens:
    """Read and validate the tokens Google returned. Raises ValueError."""
    access_token: object = getattr(credentials, "token", None)
    raw_refresh_token: object = getattr(credentials, "refresh_token", None)
    raw_expiry: object = getattr(credentials, "expiry", None)
    raw_scopes: object = getattr(credentials, "scopes", None)

    if not isinstance(access_token, str) or not access_token:
        raise ValueError("Google access token is missing.")

    refresh_token: str | None = None
    if raw_refresh_token is not None:
        if not isinstance(raw_refresh_token, str):
            raise ValueError("Google refresh token is invalid.")
        refresh_token = raw_refresh_token

    expires_at: datetime | None = None
    if isinstance(raw_expiry, datetime):
        expires_at = (
            raw_expiry
            if raw_expiry.tzinfo is not None
            else raw_expiry.replace(tzinfo=UTC)
        )

    scopes: list[str] = []
    if raw_scopes is not None and not isinstance(raw_scopes, str):
        scopes = [
            scope
            for scope in cast(Iterable[object], raw_scopes)
            if isinstance(scope, str)
        ]

    return GoogleTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scopes=scopes,
    )


def get_google_user_info(flow: OAuthFlow) -> dict[str, str]:
    raw_id_token: object = getattr(flow.credentials, "id_token", None)

    if not isinstance(raw_id_token, str) or not raw_id_token:
        raise ValueError("Google ID token is missing.")

    user_info = verify_id_token(raw_id_token, get_settings().GOOGLE_CLIENT_ID)

    subject = user_info.get("sub")
    email = user_info.get("email")

    if not isinstance(subject, str) or not isinstance(email, str):
        raise TypeError("Google account identity is incomplete.")

    if user_info.get("email_verified") is not True:
        raise ValueError("Google email is not verified.")

    return {
        "sub": subject,
        "email": email,
    }


async def revoke_google_token(token: str) -> bool:
    """Best-effort revoke at Google so a disconnect really cuts access."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(REVOKE_URI, data={"token": token})
        return response.status_code == 200
    except httpx.HTTPError:
        return False