import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from google.auth.transport import requests
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from app.core.config import get_settings

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

GOOGLE_LOGIN_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str


def _create_flow(state: str | None = None) -> Flow:
    settings = get_settings()
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_AUTH_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=GOOGLE_LOGIN_SCOPES,
        state=state,
        redirect_uri=settings.GOOGLE_AUTH_REDIRECT_URI,
        autogenerate_code_verifier=False,
    )


def get_login_authorization_url(state: str) -> str:
    flow = _create_flow(state=state)
    url, _ = flow.authorization_url(
        access_type="online",
        prompt="select_account",
    )
    return str(url)


def verify_google_id_token(raw_id_token: str) -> GoogleIdentity:
    """Blocking: fetches Google's signing certs. Run in a thread."""
    claims: Mapping[str, Any] = cast(
        Mapping[str, Any],
        id_token.verify_oauth2_token(
            raw_id_token,
            requests.Request(),
            get_settings().GOOGLE_CLIENT_ID,
        ),
    )

    sub = claims.get("sub")
    email = claims.get("email")

    if not isinstance(sub, str) or not isinstance(email, str):
        raise TypeError("Google account identity is incomplete.")

    if claims.get("email_verified") is not True:
        raise ValueError("Google email is not verified.")

    raw_name = claims.get("name")
    name = (
        raw_name.strip()
        if isinstance(raw_name, str) and raw_name.strip()
        else email.split("@", 1)[0]
    )

    return GoogleIdentity(sub=sub, email=email.lower(), name=name[:150])


def exchange_login_code(code: str) -> GoogleIdentity:
    """Blocking: trade the authorization code for a verified identity."""
    flow = _create_flow()
    flow.fetch_token(code=code)

    raw_id_token = getattr(flow.credentials, "id_token", None)
    if not isinstance(raw_id_token, str) or not raw_id_token:
        raise ValueError("Google ID token is missing.")

    return verify_google_id_token(raw_id_token)
