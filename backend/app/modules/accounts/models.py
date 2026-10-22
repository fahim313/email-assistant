import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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

    # pending | syncing | synced | failed
    sync_status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        nullable=False,
    )

    # Current sync step key (connecting | inbox | sent | threads | done)
    sync_step: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    # Set when Google rejects our refresh token (revoked / expired / removed
    # from the user's Google account). Cleared when the user reconnects.
    needs_reauth: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Gmail historyId at the start of the last sync (for incremental sync later)
    history_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    sync_total: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )

    sync_done: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )

    sync_error: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    @property
    def status(self) -> str:
        """UI status: connected | syncing | disconnected."""
        if self.needs_reauth:
            return "disconnected"
        if self.sync_status == "syncing":
            return "syncing"
        return "connected"


class OAuthState(TimeStampMixin, Base):
    __tablename__: str = "oauth_states"

    state: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
