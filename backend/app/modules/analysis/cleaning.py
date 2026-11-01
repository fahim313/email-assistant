import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.modules.emails.models import Email

URL_PATTERN = re.compile(r"https?://[^\s<>\")\]]+")

CUT_PATTERNS = (
    re.compile(
        r"\n[ \t]*On\s.{0,300}?(?:wrote|লিখেছেন)\s*:[ \t]*(?:\n|$)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\n[ \t]*-{2,}\s*Original Message\s*-{2,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\n[ \t]*From:[^\n]+\n[ \t]*Sent:",
        re.IGNORECASE,
    ),
    re.compile(r"\n-- ?\n"),
)


@dataclass(frozen=True)
class CleanedThread:
    text: str
    content_hash: str
    message_count: int


def _shorten_url(match: re.Match[str]) -> str:
    host = urlparse(match.group(0)).netloc.lower()
    return f"[link:{host}]" if host else "[link]"


def clean_body(text: str | None) -> str:
    if not text:
        return ""

    value = "\n" + text.replace("\r\n", "\n").replace("\r", "\n")

    for pattern in CUT_PATTERNS:
        match = pattern.search(value)

        if match:
            value = value[: match.start()]

    lines = [
        line.rstrip()
        for line in value.split("\n")
        if not line.lstrip().startswith(">")
    ]

    value = "\n".join(lines)
    value = value.replace("<thread>", "").replace("</thread>", "")
    value = URL_PATTERN.sub(_shorten_url, value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)

    return value.strip()


def _render_message(email: Email, body_limit: int) -> str:
    direction = "SENT by account owner" if email.is_sent else "RECEIVED"

    sender = (
        f"{email.sender_name} <{email.sender_email}>"
        if email.sender_name
        else email.sender_email
    )

    body = clean_body(email.body_text) or clean_body(email.snippet)

    if len(body) > body_limit:
        body = body[:body_limit].rstrip() + " ...[truncated]"

    lines = [
        f"[{direction}] {email.received_at.isoformat()}",
        f"From: {sender}",
    ]

    if email.to_addresses:
        lines.append(f"To: {email.to_addresses[:200]}")

    if (
        not email.is_sent
        and email.reply_to
        and email.reply_to.strip().lower() != email.sender_email.lower()
    ):
        lines.append(f"Reply-To: {email.reply_to[:200]}")

    if not email.is_sent and email.auth_results:
        lines.append(f"Auth: {email.auth_results[:200]}")

    lines.extend(["", body])

    return "\n".join(lines)


def build_thread_text(
    subject: str,
    messages: list[Email],
    max_chars: int,
) -> CleanedThread:
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")

    header = f"Subject: {subject}\nMessages in thread: {len(messages)}"

    if len(header) > max_chars:
        raise ValueError("max_chars is too small for thread metadata")

    if not messages:
        text = header
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        return CleanedThread(
            text=text,
            content_hash=digest,
            message_count=0,
        )

    available_chars = max_chars - len(header) - 2

    if available_chars <= 0:
        raise ValueError("max_chars is too small for thread messages")

    rendered: list[str] = []
    used_chars = 0

    body_limit = max(1, available_chars // 3)

    # Messages are provided oldest-first.
    # Select newest messages first, then restore chronological order.
    for email in reversed(messages):
        block = _render_message(email, body_limit)
        separator_size = 2 if rendered else 0
        required_chars = len(block) + separator_size

        if required_chars > available_chars - used_chars:
            break

        rendered.append(block)
        used_chars += required_chars

    rendered.reverse()

    omitted = len(messages) - len(rendered)

    if omitted:
        omission_note = f"\n({omitted} older messages omitted)"
        header += omission_note

    text = header

    if rendered:
        text += "\n\n" + "\n\n".join(rendered)

    if len(text) > max_chars:
        raise RuntimeError("Thread cleaning exceeded the character limit")

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    return CleanedThread(
        text=text,
        content_hash=digest,
        message_count=len(messages),
    )