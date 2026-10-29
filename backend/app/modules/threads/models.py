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


class Thread(TimeStampMixin, Base):
    """A Gmail conversation, rebuilt from its messages after every sync."""

    __tablename__: str = "threads"

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "account_id",
            "gmail_thread_id",
            name="uq_thread_account_gmail_thread",
        ),
        Index("ix_threads_account_last", "account_id", "last_message_at"),
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

    gmail_thread_id: Mapped[str] = mapped_column(String(64), nullable=False)

    subject: Mapped[str] = mapped_column(
        String(998), default="", nullable=False
    )
    snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)

    first_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    message_count: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )

    last_sender_name: Mapped[str] = mapped_column(
        String(320), default="", nullable=False
    )
    last_sender_email: Mapped[str] = mapped_column(
        String(320), default="", nullable=False
    )
    last_message_is_sent: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    has_inbox: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    is_unread: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    participants: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, nullable=False
    )