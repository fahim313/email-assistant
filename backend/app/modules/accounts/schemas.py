import uuid
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class AccountResponse(BaseModel):
    id: uuid.UUID
    email: str
    # connected | syncing | disconnected
    status: str
    needs_reauth: bool
    sync_status: str
    sync_step: str | None
    sync_done: int
    sync_total: int
    sync_error: str | None
    last_synced_at: datetime | None

    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)


class GoogleConnectResponse(BaseModel):
    authorization_url: str
