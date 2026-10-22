import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.sql.schema import SchemaItem

from app.db.base import Base


class SyncRun(Base):
    """History of sync attempts (live progress is on ConnectedAccount)."""

    __tablename__: str = "sync_runs"

    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_sync_runs_account_started", "account_id", "started_at"),
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

    # full | incremental
    mode: Mapped[str] = mapped_column(String(20), nullable=False)

    # running | success | failed
    status: Mapped[str] = mapped_column(
        String(20), default="running", nullable=False
    )

    messages_synced: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
