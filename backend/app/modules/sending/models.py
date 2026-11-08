import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from app.db.base import Base, TimeStampMixin


class EmailSend(TimeStampMixin, Base):
    __tablename__: str = "email_sends"

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_email_send_user_key",
        ),
        Index(
            "ix_email_sends_account_created",
            "account_id",
            "created_at",
        ),
        Index(
            "ix_email_sends_draft",
            "draft_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connected_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )

    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reply_drafts.id", ondelete="SET NULL"),
        nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    # pending | sent | failed | outcome_unknown
    status: Mapped[str] = mapped_column(
        String(24),
        default="pending",
        nullable=False,
    )

    draft_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    to_addresses: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        nullable=False,
    )

    cc_addresses: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        nullable=False,
    )

    bcc_addresses: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        nullable=False,
    )

    subject: Mapped[str] = mapped_column(
        String(998),
        default="",
        nullable=False,
    )

    body: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )

    in_reply_to_header: Mapped[str | None] = mapped_column(
        String(998),
        nullable=True,
    )

    references_header: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    gmail_thread_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    gmail_message_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    outcome_unknown: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )