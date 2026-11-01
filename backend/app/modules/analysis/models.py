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
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.schema import SchemaItem

from app.db.base import Base, TimeStampMixin


class ThreadAnalysis(TimeStampMixin, Base):
    __tablename__: str = "thread_analyses"

    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_thread_analyses_status", "status"),
        Index("ix_thread_analyses_priority", "priority"),
        Index("ix_thread_analyses_category", "category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        nullable=False,
    )
    category: Mapped[str] = mapped_column(
        String(32),
        default="other",
        nullable=False,
    )
    priority: Mapped[str] = mapped_column(
        String(16),
        default="medium",
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )

    is_spam: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    is_suspicious: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    risk_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    needs_reply: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    follow_up: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    has_deadline: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        default="",
        nullable=False,
    )
    model: Mapped[str] = mapped_column(
        String(100),
        default="",
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    action_items: Mapped[list["ActionItem"]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ActionItem.position",
    )


class ActionItem(TimeStampMixin, Base):
    __tablename__: str = "action_items"

    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_action_items_analysis", "analysis_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("thread_analyses.id", ondelete="CASCADE"),
        nullable=False,
    )

    text: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    done: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    position: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    analysis: Mapped[ThreadAnalysis] = relationship(
        back_populates="action_items",
    )