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
