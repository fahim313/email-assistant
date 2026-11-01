import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from app.db.base import Base, TimeStampMixin


class Email(TimeStampMixin, Base):
    """A single Gmail message (inbox or sent). body_text is untrusted input."""

    __tablename__: str = "emails"

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "account_id",
            "gmail_message_id",
            name="uq_email_account_message",
        ),
        Index("ix_emails_account_received", "account_id", "received_at"),
        Index("ix_emails_thread", "account_id", "thread_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connected_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )

    gmail_message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)

    subject: Mapped[str] = mapped_column(String(998), default="", nullable=False)
    sender_name: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    sender_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    to_addresses: Mapped[str | None] = mapped_column(Text, nullable=True)
    cc_addresses: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)

    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    message_id_header: Mapped[str | None] = mapped_column(String(998), nullable=True)
    in_reply_to_header: Mapped[str | None] = mapped_column(String(998), nullable=True)
    references_header: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    labels: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, nullable=False
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_sent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    auth_results: Mapped[str | None] = mapped_column(Text, nullable=True)
