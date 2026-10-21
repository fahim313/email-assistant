"""Thin, blocking wrapper around the Gmail API.

Everything here is synchronous (google-api-python-client), so callers
must run these functions with asyncio.to_thread().
"""

from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.core.config import get_settings

TOKEN_URI = "https://oauth2.googleapis.com/token"

METADATA_HEADERS = [
    "From",
    "To",
    "Subject",
    "Date",
    "Reply-To",
    "Authentication-Results",
]


def build_credentials(
    *,
    access_token: str | None,
    refresh_token: str | None,
    expires_at: datetime | None,
    scopes: list[str],
) -> Credentials:
    settings = get_settings()

    # google-auth expects a naive UTC datetime
    expiry = (
        expires_at.astimezone(UTC).replace(tzinfo=None) if expires_at else None
    )

    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
        scopes=scopes,
        expiry=expiry,
    )


def refresh_if_needed(creds: Credentials) -> bool:
    """Refresh the access token when missing/expired. True if refreshed."""
    if creds.valid:
        return False

    if not creds.refresh_token:
        raise ValueError("No refresh token stored. Please reconnect Gmail.")

    creds.refresh(Request())
    return True


def credentials_expiry(creds: Credentials) -> datetime | None:
    if creds.expiry is None:
        return None
    return creds.expiry.replace(tzinfo=UTC)


def build_service(creds: Credentials) -> Any:
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_profile(service: Any) -> dict[str, Any]:
    return service.users().getProfile(userId="me").execute()


def list_message_ids(service: Any, query: str, max_total: int) -> list[str]:
    ids: list[str] = []
    page_token: str | None = None

    while len(ids) < max_total:
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=min(100, max_total - len(ids)),
                pageToken=page_token,
            )
            .execute()
        )
        ids.extend(m["id"] for m in response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return ids


def fetch_messages(service: Any, ids: list[str]) -> list[dict[str, Any]]:
    """Fetch metadata for many messages with one batch HTTP request."""
    results: dict[str, dict[str, Any]] = {}

    def _callback(request_id: str, response: Any, exception: Any) -> None:
        if exception is None:
            results[request_id] = response

    batch = service.new_batch_http_request(callback=_callback)
    for message_id in ids:
        batch.add(
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=METADATA_HEADERS,
            ),
            request_id=message_id,
        )
    batch.execute()

    return [parse_message(results[i]) for i in ids if i in results]


def parse_message(raw: dict[str, Any]) -> dict[str, Any]:
    headers = {
        h["name"].lower(): h["value"]
        for h in raw.get("payload", {}).get("headers", [])
    }
    sender_name, sender_email = parseaddr(headers.get("from", ""))
    labels = raw.get("labelIds", [])
    received_ms = int(raw.get("internalDate", "0"))

    return {
        "gmail_message_id": raw["id"],
        "thread_id": raw["threadId"],
        "subject": headers.get("subject", "")[:998],
        "sender_name": sender_name[:320],
        "sender_email": sender_email[:320],
        "reply_to": headers.get("reply-to"),
        "auth_results": headers.get("authentication-results"),
        "snippet": raw.get("snippet", ""),
        "received_at": datetime.fromtimestamp(received_ms / 1000, tz=UTC),
        "labels": labels,
        "is_read": "UNREAD" not in labels,
    }
