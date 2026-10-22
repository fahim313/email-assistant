import os
from collections.abc import Mapping
from typing import Any, cast

import httpx
from google.auth.transport import requests
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from app.core.config import get_settings

# The user can untick Gmail permissions on Google's consent screen
# (granular consent). oauthlib would then raise a bare Warning from
# fetch_token(); relax that so we can check the granted scopes ourselves
# and show a proper message.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

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


def missing_gmail_scopes(granted: list[str]) -> list[str]:
    return [scope for scope in GMAIL_SCOPES if scope not in set(granted)]


def create_google_flow(state: str | None = None) -> Flow:
    settings = get_settings()

    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_GMAIL_REDIRECT_URI],
        }
    }

    return Flow.from_client_config(
        client_config,
        scopes=GOOGLE_SCOPES,
        state=state,
        redirect_uri=settings.GOOGLE_GMAIL_REDIRECT_URI,
        # connect and callback use separate Flow objects, so a PKCE
        # code_verifier would be lost between them
        autogenerate_code_verifier=False,
    )


def get_google_authorization_url(state: str) -> str:
    flow = create_google_flow(state=state)

    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        # select_account lets the user pick a different Gmail each time;
        # consent makes Google return a refresh token every time.
        prompt="select_account consent",
    )

    return str(authorization_url)


def exchange_google_code(code: str, state: str) -> Flow:
    flow = create_google_flow(state=state)
    flow.fetch_token(code=code)
    return flow


def get_google_user_info(flow: Flow) -> dict[str, str]:
    raw_id_token = getattr(flow.credentials, "id_token", None)

    if not isinstance(raw_id_token, str) or not raw_id_token:
        raise ValueError("Google ID token is missing.")

    user_info: Mapping[str, Any] = cast(
        Mapping[str, Any],
        id_token.verify_oauth2_token(
            raw_id_token,
            requests.Request(),
            get_settings().GOOGLE_CLIENT_ID,
        ),
    )

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
