from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationOut(BaseModel):
    id: UUID
    user_id: UUID
    is_read: bool
    created_at: datetime
    metadata: dict[str, Any] | None = Field(default=None, alias="meta")
    read_at: datetime | None = None
    reference_id: UUID | None = None
    type: str
    title: str
    message: str
    reference_type: str | None = None

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )


class NotificationMessage(BaseModel):
    message: str


class NotificationReadResponse(BaseModel):
    message: str
    notification_id: UUID
    is_read: bool
    read_at: datetime | None = None


class NotificationReadAllResponse(BaseModel):
    message: str
    updated_count: int


class NotificationSocketMessage(BaseModel):
    type: Literal[
        "connected",
        "notification_created",
        "notification_updated",
        "notification_read",
        "notification_deleted",
        "notifications_read_all",
        "unread_count_updated",
        "pong",
    ]
    notification: NotificationOut | None = None
    notifications: list[NotificationOut] | None = None
    notification_id: UUID | None = None
    unread_count: int | None = None
    message: str | None = None
    timestamp: datetime | None = None