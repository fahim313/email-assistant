import os
from collections.abc import Mapping
from typing import Protocol, cast

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

# The user can untick Gmail permissions on Google's consent screen
# (granular consent). oauthlib would then raise a bare Warning from
# fetch_token(); relax that so we can check the granted scopes ourselves
# and show a proper message.
_ = os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class OAuthFlow(Protocol):
    """The part of google_auth_oauthlib.flow.Flow that we use."""

    @property
    def credentials(self) -> Credentials: ...

    def authorization_url(self, **kwargs: str) -> tuple[str, str]: ...

    def fetch_token(self, *, code: str) -> None: ...


def create_flow(
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    scopes: list[str],
    state: str | None = None,
) -> OAuthFlow:
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }

    flow = Flow.from_client_config(  # pyright: ignore[reportUnknownMemberType]
        client_config,
        scopes=scopes,
        state=state,
        redirect_uri=redirect_uri,
        # connect and callback use separate Flow objects, so a PKCE
        # code_verifier would be lost between them
        autogenerate_code_verifier=False,
    )

    return cast(OAuthFlow, cast(object, flow))


def verify_id_token(raw_id_token: str, audience: str) -> Mapping[str, object]:
    """Blocking: fetches Google's signing certs. Run in a thread."""
    claims = google_id_token.verify_oauth2_token(  # pyright: ignore[reportUnknownMemberType]
        raw_id_token,
        google_requests.Request(),
        audience,
    )
    return claims
