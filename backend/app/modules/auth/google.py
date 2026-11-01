from dataclasses import dataclass

from app.core.config import get_settings
from app.integrations.google_client import (
    OAuthFlow,
    create_flow,
    verify_id_token,
)

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


def _create_flow(state: str | None = None) -> OAuthFlow:
    settings = get_settings()

    return create_flow(
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
        redirect_uri=settings.GOOGLE_AUTH_REDIRECT_URI,
        scopes=GOOGLE_LOGIN_SCOPES,
        state=state,
    )


def get_login_authorization_url(state: str) -> str:
    flow = _create_flow(state=state)
    url, _ = flow.authorization_url(
        access_type="online",
        prompt="select_account",
    )
    return url


def verify_google_id_token(raw_id_token: str) -> GoogleIdentity:
    """Blocking: fetches Google's signing certs. Run in a thread."""
    claims = verify_id_token(raw_id_token, get_settings().GOOGLE_CLIENT_ID)

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

    raw_id_token: object = getattr(flow.credentials, "id_token", None)
    if not isinstance(raw_id_token, str) or not raw_id_token:
        raise ValueError("Google ID token is missing.")

    return verify_google_id_token(raw_id_token)
