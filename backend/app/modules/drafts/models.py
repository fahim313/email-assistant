import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.schema import SchemaItem

from app.db.base import Base, TimeStampMixin


class ReplyDraft(TimeStampMixin, Base):
    __tablename__: str = "reply_drafts"

    __table_args__: tuple[SchemaItem, ...] = (
        Index(
            "ix_reply_drafts_account_updated",
            "account_id",
            "updated_at",
        ),
        Index(
            "ix_reply_drafts_thread",
            "thread_id",
        ),
        Index(
            "ix_reply_drafts_gmail_id",
            "gmail_draft_id",
        ),
        Index(
            "ix_reply_drafts_gmail_message_id",
            "gmail_message_id",
        ),
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

    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="SET NULL"),
        nullable=True,
    )

    reply_to_email_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("emails.id", ondelete="SET NULL"),
        nullable=True,
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

    # draft | sent
    status: Mapped[str] = mapped_column(
        String(16),
        default="draft",
        nullable=False,
    )

    # ai | manual
    source: Mapped[str] = mapped_column(
        String(16),
        default="manual",
        nullable=False,
    )

    current_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    model: Mapped[str] = mapped_column(
        String(100),
        default="",
        nullable=False,
    )

    gmail_draft_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    # pending | synced | failed
    gmail_sync_status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default="pending",
        nullable=False,
    )

    gmail_sync_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    gmail_message_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    versions: Mapped[list["ReplyDraftVersion"]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ReplyDraftVersion.version_number",
    )


class ReplyDraftVersion(TimeStampMixin, Base):
    __tablename__: str = "reply_draft_versions"

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "draft_id",
            "version_number",
            name="uq_reply_draft_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reply_drafts.id", ondelete="CASCADE"),
        nullable=False,
    )

    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # ai_generate | ai_regenerate | manual_create | manual_edit
    source: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )

    instruction: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    model: Mapped[str] = mapped_column(
        String(100),
        default="",
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

    draft: Mapped[ReplyDraft] = relationship(
        back_populates="versions",
    )