import base64
import html as html_lib
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from typing import (
    ClassVar,
    Protocol,
    Required,
    TypedDict,
    cast,
    final,
    override,
)

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build  # pyright: ignore[reportUnknownVariableType]
from googleapiclient.errors import HttpError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"
MAX_BODY_CHARS = 20_000

WANTED_LABELS = {"INBOX", "SENT"}
UNWANTED_LABELS = {"SPAM", "TRASH", "DRAFT"}

_QUOTA_MARKERS = (
    "quota",
    "ratelimitexceeded",
    "rate limit",
    "user rate limit",
)


class GmailProfile(TypedDict, total=False):
    emailAddress: str
    historyId: str


class MessageRef(TypedDict):
    id: str


class MessageListResponse(TypedDict, total=False):
    messages: list[MessageRef]
    nextPageToken: str


class HistoryMessageRef(TypedDict):
    message: MessageRef


class HistoryRecord(TypedDict, total=False):
    messagesAdded: list[HistoryMessageRef]
    messagesDeleted: list[HistoryMessageRef]
    labelsAdded: list[HistoryMessageRef]
    labelsRemoved: list[HistoryMessageRef]


class HistoryListResponse(TypedDict, total=False):
    history: list[HistoryRecord]
    nextPageToken: str


class GmailHeader(TypedDict):
    name: str
    value: str


class GmailBody(TypedDict, total=False):
    data: str


class GmailPayload(TypedDict, total=False):
    mimeType: str
    filename: str
    headers: list[GmailHeader]
    body: GmailBody
    parts: list["GmailPayload"]


class GmailMessage(TypedDict, total=False):
    id: Required[str]
    threadId: Required[str]
    internalDate: str
    snippet: str
    labelIds: list[str]
    payload: GmailPayload


class _Request[T](Protocol):
    def execute(self) -> T: ...


type _BatchCallback = Callable[
    [str, GmailMessage | None, Exception | None],
    None,
]


class _Batch(Protocol):
    def add(
        self,
        request: _Request[GmailMessage],
        *,
        request_id: str,
    ) -> None: ...

    def execute(self) -> None: ...


class _Messages(Protocol):
    def list(
        self,
        *,
        userId: str,
        q: str,
        maxResults: int,
        pageToken: str | None,
    ) -> _Request[MessageListResponse]: ...

    def get(
        self,
        *,
        userId: str,
        id: str,
        format: str,
    ) -> _Request[GmailMessage]: ...


class _History(Protocol):
    def list(
        self,
        *,
        userId: str,
        startHistoryId: str,
        historyTypes: Sequence[str],
        maxResults: int,
        pageToken: str | None,
    ) -> _Request[HistoryListResponse]: ...


class _Users(Protocol):
    def getProfile(self, *, userId: str) -> _Request[GmailProfile]: ...

    def messages(self) -> _Messages: ...

    def history(self) -> _History: ...


class GmailService(Protocol):
    def users(self) -> _Users: ...

    def new_batch_http_request(
        self,
        *,
        callback: _BatchCallback,
    ) -> _Batch: ...


class EmailUpsertData(TypedDict):
    gmail_message_id: str
    thread_id: str
    subject: str
    sender_name: str
    sender_email: str
    to_addresses: str | None
    cc_addresses: str | None
    reply_to: str | None
    message_id_header: str | None
    in_reply_to_header: str | None
    references_header: str | None
    auth_results: str | None
    snippet: str
    body_text: str | None
    received_at: datetime
    labels: list[str]
    is_read: bool
    is_sent: bool


def http_status(exc: object) -> int | None:
    resp: object = getattr(exc, "resp", None)
    status: object = getattr(resp, "status", None)
    return status if isinstance(status, int) else None


def is_quota_error(exc: object) -> bool:
    status = http_status(exc)

    if status == 429:
        return True

    return status == 403 and any(
        marker in str(exc).lower() for marker in _QUOTA_MARKERS
    )


def build_credentials(
    *,
    access_token: str | None,
    refresh_token: str | None,
    expires_at: datetime | None,
    scopes: list[str],
) -> Credentials:
    settings = get_settings()

    expiry = (
        expires_at.astimezone(UTC).replace(tzinfo=None)
        if expires_at
        else None
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
    if creds.valid:
        return False

    # google-auth does not ship precise types for these members.
    refresh_token = cast(str | None, creds.refresh_token)
    if not refresh_token:
        raise ValueError("No refresh token stored. Please reconnect Gmail.")

    creds.refresh(Request())  # pyright: ignore[reportUnknownMemberType]
    return True


def credentials_expiry(creds: Credentials) -> datetime | None:
    expiry = cast(datetime | None, creds.expiry)
    if expiry is None:
        return None

    return expiry.replace(tzinfo=UTC)


def credentials_token(creds: Credentials) -> str:
    token = cast(str | None, creds.token)
    if not token:
        raise ValueError("Google access token is missing.")

    return token


def build_service(creds: Credentials) -> GmailService:
    resource = build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,
    )
    return cast(GmailService, cast(object, resource))


def get_profile(service: GmailService) -> GmailProfile:
    return service.users().getProfile(userId="me").execute()


def is_wanted(labels: list[str]) -> bool:
    label_set = set(labels)

    return bool(label_set & WANTED_LABELS) and not (
        label_set & UNWANTED_LABELS
    )


def list_message_ids(
    service: GmailService,
    query: str,
    max_total: int,
) -> list[str]:
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

        ids.extend(message["id"] for message in response.get("messages", []))

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return ids[:max_total]


@dataclass
class HistoryChanges:
    added: set[str] = field(default_factory=set)
    changed: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)


class HistoryExpiredError(Exception):
    pass


def list_history(
    service: GmailService,
    start_history_id: str,
) -> HistoryChanges:
    changes = HistoryChanges()
    page_token: str | None = None

    while True:
        try:
            response = (
                service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=[
                        "messageAdded",
                        "messageDeleted",
                        "labelAdded",
                        "labelRemoved",
                    ],
                    maxResults=500,
                    pageToken=page_token,
                )
                .execute()
            )
        except HttpError as exc:
            if http_status(exc) == 404:
                raise HistoryExpiredError from exc

            raise

        for record in response.get("history", []):
            for item in record.get("messagesAdded", []):
                changes.added.add(item["message"]["id"])

            label_changes = [
                *record.get("labelsAdded", []),
                *record.get("labelsRemoved", []),
            ]
            for item in label_changes:
                changes.changed.add(item["message"]["id"])

            for item in record.get("messagesDeleted", []):
                changes.deleted.add(item["message"]["id"])

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    changes.added -= changes.deleted
    changes.changed -= changes.deleted

    return changes


def fetch_messages(
    service: GmailService,
    ids: list[str],
) -> tuple[list[EmailUpsertData], list[str], list[str], bool]:
    """Fetch messages and return parsed results, retry IDs, missing IDs,
    and whether Gmail quota was exceeded.
    """
    if not ids:
        return [], [], [], False

    results: dict[str, GmailMessage] = {}
    retry: list[str] = []
    missing: list[str] = []
    quota_limited = False

    def _callback(
        request_id: str,
        response: GmailMessage | None,
        exception: Exception | None,
    ) -> None:
        nonlocal quota_limited

        if exception is None:
            if response is not None:
                results[request_id] = response
            else:
                retry.append(request_id)

            return

        status = http_status(exception)

        if status == 404:
            missing.append(request_id)
            return

        if is_quota_error(exception):
            quota_limited = True

        logger.warning(
            "Gmail message fetch failed: status=%s, error_type=%s, error=%s",
            status,
            type(exception).__name__,
            str(exception)[:300],
        )

        retry.append(request_id)

    batch = service.new_batch_http_request(callback=_callback)

    for message_id in ids:
        batch.add(
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="full",
            ),
            request_id=message_id,
        )

    try:
        batch.execute()
    except HttpError as exc:
        status = http_status(exc)

        if status == 404:
            missing.extend(
                message_id
                for message_id in ids
                if message_id not in results and message_id not in missing
            )

            logger.warning("Gmail batch request returned 404.")
        else:
            quota_limited = is_quota_error(exc)

            logger.warning(
                "Gmail batch request failed: status=%s, error=%s",
                status,
                str(exc)[:300],
            )

            retry.extend(
                message_id
                for message_id in ids
                if message_id not in results
                and message_id not in missing
                and message_id not in retry
            )

    parsed_messages = [
        parse_message(results[message_id])
        for message_id in ids
        if message_id in results
    ]

    return parsed_messages, retry, missing, quota_limited


@final
class _TextExtractor(HTMLParser):
    _SKIP: ClassVar[set[str]] = {
        "script",
        "style",
        "head",
        "title",
    }

    _BREAK: ClassVar[set[str]] = {
        "br",
        "p",
        "div",
        "tr",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "blockquote",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    @override
    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    @override
    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()

    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)

    return text.strip()


def _decode_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)

    return base64.urlsafe_b64decode(padded).decode(
        "utf-8",
        errors="replace",
    )


def _collect_parts(
    payload: GmailPayload,
    out: dict[str, list[str]],
) -> None:
    mime = payload.get("mimeType", "")
    body = payload.get("body")
    body_data = body.get("data") if body else None
    is_attachment = bool(payload.get("filename"))

    if (
        body_data
        and not is_attachment
        and mime in ("text/plain", "text/html")
    ):
        out.setdefault(mime, []).append(_decode_body(body_data))

    for part in payload.get("parts", []):
        _collect_parts(part, out)


def extract_body(payload: GmailPayload) -> str:
    found: dict[str, list[str]] = {}
    _collect_parts(payload, found)

    if found.get("text/plain"):
        text = "\n".join(found["text/plain"]).strip()
    elif found.get("text/html"):
        text = html_to_text("\n".join(found["text/html"]))
    else:
        text = ""

    return text[:MAX_BODY_CHARS]


def _addresses(value: str | None) -> str | None:
    if not value:
        return None

    emails = [
        address.lower()
        for _, address in getaddresses([value])
        if address
    ]

    return ", ".join(emails) or None


def parse_message(raw: GmailMessage) -> EmailUpsertData:
    payload: GmailPayload = raw.get("payload") or {}

    headers: dict[str, str] = {}
    auth_parts: list[str] = []

    for header in payload.get("headers", []):
        name = header["name"]
        value = header["value"]
        lowered = name.lower()

        if lowered in ("authentication-results", "received-spf"):
            auth_parts.append(f"{name}: {value}")
        elif lowered not in headers:
            headers[lowered] = value

    sender_name, sender_email = parseaddr(headers.get("from", ""))

    labels = list(raw.get("labelIds", []))
    received_ms = int(raw.get("internalDate", "0"))

    return {
        "gmail_message_id": raw["id"],
        "thread_id": raw["threadId"],
        "subject": headers.get("subject", "")[:998],
        "sender_name": sender_name[:320],
        "sender_email": sender_email.lower()[:320],
        "to_addresses": _addresses(headers.get("to")),
        "cc_addresses": _addresses(headers.get("cc")),
        "reply_to": headers.get("reply-to"),
        "message_id_header": (headers.get("message-id") or "")[:998] or None,
        "in_reply_to_header": (headers.get("in-reply-to") or "")[:998] or None,
        "references_header": headers.get("references"),
        "auth_results": "\n".join(auth_parts) or None,
        "snippet": html_lib.unescape(raw.get("snippet", "")),
        "body_text": extract_body(payload) or None,
        "received_at": datetime.fromtimestamp(received_ms / 1000, tz=UTC),
        "labels": labels,
        "is_read": "UNREAD" not in labels,
        "is_sent": "SENT" in labels,
    }