import uuid
from datetime import datetime

from pydantic import BaseModel


class EmailItem(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    account_email: str
    thread_id: str
    subject: str
    sender_name: str
    sender_email: str
    snippet: str
    received_at: datetime
    labels: list[str]
    is_read: bool


class EmailPage(BaseModel):
    items: list[EmailItem]
    total: int
    page: int
    page_size: int


class MessageItem(BaseModel):
    id: uuid.UUID
    subject: str
    sender_name: str
    sender_email: str
    to: list[str]
    cc: list[str]
    snippet: str
    body_text: str | None
    received_at: datetime
    is_read: bool
    is_sent: bool
    labels: list[str]


class ThreadItem(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    account_email: str
    subject: str
    snippet: str
    last_message_at: datetime
    message_count: int
    last_sender_name: str
    last_sender_email: str
    last_message_is_sent: bool
    is_unread: bool
    participants: list[str]


class ThreadPage(BaseModel):
    items: list[ThreadItem]
    total: int
    page: int
    page_size: int


class ThreadDetail(ThreadItem):
    messages: list[MessageItem]