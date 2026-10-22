import uuid
from datetime import datetime

from pydantic import BaseModel


class SyncStepStatus(BaseModel):
    key: str
    label: str
    # done | active | pending | failed
    state: str


class SyncStatusResponse(BaseModel):
    account_id: uuid.UUID
    # pending | syncing | synced | failed
    status: str
    step: str | None
    steps: list[SyncStepStatus]
    done: int
    total: int
    progress: int  # 0-100
    error: str | None
    needs_reauth: bool
    last_synced_at: datetime | None
