import uuid
from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

SendStatus = Literal[
    "pending",
    "sent",
    "failed",
    "outcome_unknown",
]


class SendRequest(BaseModel):
    draft_id: uuid.UUID
    confirm: Literal[True]


class SendItem(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID | None
    account_id: uuid.UUID
    status: SendStatus

    to_addresses: list[str]
    cc_addresses: list[str]
    bcc_addresses: list[str]

    subject: str
    draft_version: int

    gmail_message_id: str | None
    gmail_thread_id: str | None

    error_message: str | None
    outcome_unknown: bool
    attempts: int

    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SendDetail(SendItem):
    body: str


class SendPage(BaseModel):
    items: list[SendItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)