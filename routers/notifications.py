from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.security import require_user
from models.user import User
from schemas.notification import (
    NotificationOut,
    NotificationReadAllResponse,
    NotificationReadResponse,
)
from services.notification_service import (
    get_latest_notifications_service,
    get_my_notifications_service,
    mark_all_notifications_as_read_service,
    mark_notification_as_read_service,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/latest", response_model=list[NotificationOut])
async def get_latest_notifications(
    limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    notifications = await get_latest_notifications_service(
        db=db,
        user_id=current_user.id,
        limit=limit,
    )
    return notifications


@router.get("/me", response_model=list[NotificationOut])
async def get_my_notifications(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    notifications = await get_my_notifications_service(
        db=db,
        user_id=current_user.id,
        limit=20,
    )
    return notifications


@router.patch("/{notification_id}/read", response_model=NotificationReadResponse)
async def mark_notification_as_read(
    notification_id: UUID,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    notification = await mark_notification_as_read_service(
        db=db,
        user_id=current_user.id,
        notification_id=notification_id,
    )

    return NotificationReadResponse(
        message="알림을 읽음 처리했습니다.",
        notification_id=notification.id,
        is_read=notification.is_read,
        read_at=notification.read_at,
    )


@router.patch("/read-all", response_model=NotificationReadAllResponse)
async def mark_all_notifications_as_read(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    updated_count = await mark_all_notifications_as_read_service(
        db=db,
        user_id=current_user.id,
    )

    return NotificationReadAllResponse(
        message="전체 알림을 읽음 처리했습니다.",
        updated_count=updated_count,
    )