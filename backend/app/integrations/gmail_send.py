import base64
import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from typing import cast, final
from urllib.parse import quote

import httpx

GMAIL_API_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_PLACEHOLDER_RE = re.compile(r"\[[^\[\]\n]{2,80}\]")
_BAD_ADDRESS_RE = re.compile(r"[\r\n,<>\s]")


class GmailSendError(Exception):
    status_code: int | None
    # True when the request may have reached Gmail (do not retry blindly).
    ambiguous: bool

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.ambiguous = ambiguous


@dataclass(frozen=True, slots=True)
class GmailDraftRef:
    draft_id: str
    message_id: str
    thread_id: str


@dataclass(frozen=True, slots=True)
class GmailSentRef:
    message_id: str
    thread_id: str


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _check_addresses(
    label: str,
    addresses: list[str],
    *,
    required: bool = False,
) -> list[str]:
    cleaned: list[str] = []

    for raw_address in addresses:
        address = raw_address.strip()

        if not address:
            continue

        if _BAD_ADDRESS_RE.search(address):
            raise ValueError(f"Invalid {label} email address.")

        _, parsed_address = parseaddr(address)

        if (
            not parsed_address
            or parsed_address != address
            or "@" not in parsed_address
        ):
            raise ValueError(f"Invalid {label} email address.")

        cleaned.append(address)

    if required and not cleaned:
        raise ValueError(f"At least one {label} email address is required.")

    return cleaned


def reply_headers(
    message_id_header: str | None,
    references_header: str | None,
) -> tuple[str | None, str | None]:
    # _one_line also removes any line breaks, so a crafted Message-ID
    # header from an incoming email cannot inject extra headers.
    parent = _one_line(message_id_header or "")
    references = _one_line(references_header or "")

    if not parent:
        return None, references or None

    reference_ids = references.split()

    if parent not in reference_ids:
        reference_ids.append(parent)

    return parent, " ".join(reference_ids)


def find_placeholders(body: str) -> list[str]:
    return _PLACEHOLDER_RE.findall(body)


def build_raw_message(
    *,
    sender: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: str | None = None,
    require_recipient: bool = False,
) -> str:
    sender_list = _check_addresses("sender", [sender], required=True)
    to_list = _check_addresses(
        "recipient",
        to,
        required=require_recipient,
    )
    cc_list = _check_addresses("CC", cc)
    bcc_list = _check_addresses("BCC", bcc)

    message = EmailMessage()

    message["From"] = sender_list[0]

    if to_list:
        message["To"] = ", ".join(to_list)

    if cc_list:
        message["Cc"] = ", ".join(cc_list)

    if bcc_list:
        message["Bcc"] = ", ".join(bcc_list)

    message["Subject"] = _one_line(subject)

    if in_reply_to:
        if "\r" in in_reply_to or "\n" in in_reply_to:
            raise ValueError("Invalid In-Reply-To header.")

        message["In-Reply-To"] = _one_line(in_reply_to)

    if references:
        if "\r" in references or "\n" in references:
            raise ValueError("Invalid References header.")

        message["References"] = _one_line(references)

    normalized_body = body.replace("\r\n", "\n").replace("\r", "\n")
    message.set_content(normalized_body)

    raw_bytes = base64.urlsafe_b64encode(message.as_bytes())
    return raw_bytes.decode("ascii")


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_dict(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return cast(dict[str, object], value)

    return None


def _error_message(response: httpx.Response) -> str:
    fallback = f"Gmail API request failed with status {response.status_code}."

    try:
        data = _as_dict(cast(object, response.json()))
    except ValueError:
        return fallback

    error = _as_dict(data.get("error")) if data is not None else None

    if error is not None:
        message = error.get("message")

        if isinstance(message, str) and message:
            return message[:300]

    return fallback


@final
class GmailSendClient:
    _access_token: str
    _timeout: httpx.Timeout

    def __init__(
        self,
        access_token: str,
        *,
        timeout: httpx.Timeout = REQUEST_TIMEOUT,
    ) -> None:
        if not access_token:
            raise ValueError("Gmail access token is required.")

        self._access_token = access_token
        self._timeout = timeout

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
    ) -> dict[str, object]:
        url = f"{GMAIL_API_URL}/{path.lstrip('/')}"

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    json=json,
                )

        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise GmailSendError(
                "Could not connect to Gmail API.",
                ambiguous=False,
            ) from exc

        except httpx.HTTPError as exc:
            raise GmailSendError(
                "Gmail API request failed.",
                ambiguous=True,
            ) from exc

        if response.status_code >= 400:
            raise GmailSendError(
                _error_message(response),
                status_code=response.status_code,
                ambiguous=response.status_code >= 500,
            )

        if not response.content:
            return {}

        try:
            parsed = cast(object, response.json())
        except ValueError as exc:
            raise GmailSendError(
                "Gmail API returned an invalid response.",
                status_code=response.status_code,
                ambiguous=True,
            ) from exc

        data = _as_dict(parsed)

        if data is None:
            raise GmailSendError(
                "Gmail API returned an unexpected response.",
                status_code=response.status_code,
                ambiguous=True,
            )

        return data

    @staticmethod
    def _message_payload(
        raw: str,
        thread_id: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"raw": raw}

        if thread_id:
            payload["threadId"] = thread_id

        return payload

    @staticmethod
    def _draft_ref(data: dict[str, object]) -> GmailDraftRef:
        draft_id = _text(data.get("id"))

        message = _as_dict(data.get("message"))
        message_data: dict[str, object] = (
            message if message is not None else {}
        )

        message_id = _text(message_data.get("id"))
        thread_id = _text(message_data.get("threadId"))

        if not draft_id or not message_id or not thread_id:
            raise GmailSendError(
                "Gmail returned an incomplete draft response.",
                ambiguous=True,
            )

        return GmailDraftRef(
            draft_id=draft_id,
            message_id=message_id,
            thread_id=thread_id,
        )

    async def create_draft(
        self,
        raw: str,
        *,
        thread_id: str | None = None,
    ) -> GmailDraftRef:
        data = await self._request(
            "POST",
            "drafts",
            json={
                "message": self._message_payload(raw, thread_id),
            },
        )

        return self._draft_ref(data)

    async def update_draft(
        self,
        draft_id: str,
        raw: str,
        *,
        thread_id: str | None = None,
    ) -> GmailDraftRef:
        if not draft_id:
            raise ValueError("Gmail draft ID is required.")

        data = await self._request(
            "PUT",
            f"drafts/{quote(draft_id, safe='')}",
            json={
                "message": self._message_payload(raw, thread_id),
            },
        )

        return self._draft_ref(data)

    async def delete_draft(self, draft_id: str) -> None:
        if not draft_id:
            raise ValueError("Gmail draft ID is required.")

        _ = await self._request(
            "DELETE",
            f"drafts/{quote(draft_id, safe='')}",
        )

    async def send_message(
        self,
        raw: str,
        *,
        thread_id: str | None = None,
    ) -> GmailSentRef:
        data = await self._request(
            "POST",
            "messages/send",
            json=self._message_payload(raw, thread_id),
        )

        message_id = _text(data.get("id"))
        returned_thread_id = _text(data.get("threadId"))

        if not message_id:
            raise GmailSendError(
                "Gmail did not return a sent message ID.",
                ambiguous=True,
            )

        return GmailSentRef(
            message_id=message_id,
            thread_id=returned_thread_id,
        )