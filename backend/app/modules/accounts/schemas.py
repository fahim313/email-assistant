import uuid
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class AccountResponse(BaseModel):
    id: uuid.UUID
    email: str
    sync_status: str
    sync_done: int
    sync_total: int
    sync_error: str | None
    last_synced_at: datetime | None

    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)


class SyncStatusResponse(BaseModel):
    account_id: uuid.UUID
    status: str
    done: int
    total: int
    progress: int  # 0-100
    error: str | None
    last_synced_at: datetime | None


class GoogleConnectResponse(BaseModel):
    authorization_url: str
