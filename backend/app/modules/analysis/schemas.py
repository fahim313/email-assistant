import uuid
from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "work",
    "personal",
    "finance",
    "shopping",
    "newsletter",
    "social",
    "notification",
    "travel",
    "support",
    "other",
]

Priority = Literal["urgent", "medium", "low"]
AnalysisState = Literal["done", "failed"]


class ActionItemOutput(BaseModel):
    text: str = Field(
        min_length=1,
        description="One concrete task for the mailbox owner",
    )
    due_date: datetime | None = Field(
        default=None,
        description="ISO 8601 date or datetime if a due date is stated",
    )


class AnalysisOutput(BaseModel):
    category: Category
    priority: Priority
    summary: str = Field(min_length=1)
    is_spam: bool
    is_suspicious: bool
    risk_reason: str | None = None
    needs_reply: bool
    follow_up: bool
    has_deadline: bool
    deadline_at: datetime | None = None
    action_items: list[ActionItemOutput] = Field(
        default_factory=list,
        max_length=5,
    )


class ActionItemItem(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    id: uuid.UUID
    text: str
    due_date: datetime | None
    done: bool


class AnalysisBrief(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    status: AnalysisState
    category: Category
    priority: Priority
    summary: str
    is_spam: bool
    is_suspicious: bool
    needs_reply: bool
    follow_up: bool
    has_deadline: bool
    deadline_at: datetime | None
    analyzed_at: datetime


class AnalysisItem(AnalysisBrief):
    id: uuid.UUID
    thread_id: uuid.UUID
    risk_reason: str | None
    model: str
    error: str | None
    action_items: list[ActionItemItem]


class AnalysisStatus(BaseModel):
    account_id: uuid.UUID
    total: int
    analyzed: int
    failed: int
    pending: int


class BatchStarted(BaseModel):
    account_id: uuid.UUID
    queued: int
    started: bool


class ActionItemUpdate(BaseModel):
    done: bool


class AnalysisPreview(BaseModel):
    thread_id: uuid.UUID
    char_count: int
    content_hash: str
    system_prompt: str
    user_prompt: str