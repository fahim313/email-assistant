import uuid
from datetime import datetime
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

ADDRESS_PATTERN = (
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$"
)
MAX_BODY_CHARS = 20000
MAX_ADDRESSES = 50

Address = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=320,
        pattern=ADDRESS_PATTERN,
    ),
]

AddressList = Annotated[list[Address], Field(max_length=MAX_ADDRESSES)]

DraftStatus = Literal["draft", "sent"]
DraftSource = Literal["ai", "manual"]
DraftSyncStatus = Literal["pending", "synced", "failed"]

VersionSource = Literal[
    "ai_generate",
    "ai_regenerate",
    "manual_create",
    "manual_edit",
]


class ReplyOutput(BaseModel):
    body: str = Field(
        min_length=1,
        max_length=MAX_BODY_CHARS,
        description=(
            "The reply email body only, without subject, "
            "headers or quoted history"
        ),
    )


class DraftGenerateRequest(BaseModel):
    instruction: str | None = Field(default=None, max_length=1000)


class DraftCreate(BaseModel):
    account_id: uuid.UUID
    thread_id: uuid.UUID | None = None
    reply_to_email_id: uuid.UUID | None = None
    to: AddressList = Field(default_factory=list)
    cc: AddressList = Field(default_factory=list)
    bcc: AddressList = Field(default_factory=list)
    subject: str = Field(default="", max_length=998)
    body: str = Field(default="", max_length=MAX_BODY_CHARS)

    @model_validator(mode="after")
    def validate_reply_target(self) -> Self:
        if self.reply_to_email_id is not None and self.thread_id is None:
            raise ValueError(
                "thread_id is required when reply_to_email_id is set"
            )

        return self


class DraftUpdate(BaseModel):
    to: AddressList | None = None
    cc: AddressList | None = None
    bcc: AddressList | None = None
    subject: str | None = Field(default=None, max_length=998)
    body: str | None = Field(default=None, max_length=MAX_BODY_CHARS)

    @model_validator(mode="after")
    def validate_fields(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field is required")

        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")

        return self


class DraftRegenerateRequest(BaseModel):
    instruction: str | None = Field(default=None, max_length=1000)


class DraftVersionItem(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_number: int
    source: VersionSource
    instruction: str | None
    model: str
    subject: str
    body: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    created_at: datetime


class DraftItem(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    thread_id: uuid.UUID | None
    reply_to_email_id: uuid.UUID | None
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body: str
    status: DraftStatus
    source: DraftSource
    current_version: int
    gmail_draft_id: str | None
    gmail_sync_status: DraftSyncStatus
    gmail_sync_error: str | None
    model: str
    sent_at: datetime | None
    gmail_message_id: str | None
    created_at: datetime
    updated_at: datetime


class DraftDetail(DraftItem):
    versions: list[DraftVersionItem]


class DraftPage(BaseModel):
    items: list[DraftItem]
    total: int
    page: int
    page_size: int