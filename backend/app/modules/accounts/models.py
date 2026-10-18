import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from app.db.base import Base, TimeStampMixin


class ConnectedAccount(TimeStampMixin, Base):
    __tablename__: str = "connected_accounts"

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "user_id",
            "google_sub",
            name="uq_connected_account_user_google_sub",
        ),
        Index(
            "ix_connected_accounts_user_id",
            "user_id",
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

    google_sub: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    access_token_encrypted: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    refresh_token_encrypted: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    scopes: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    sync_status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        nullable=False,
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )