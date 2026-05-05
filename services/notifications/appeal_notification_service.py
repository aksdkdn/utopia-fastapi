from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.admin import AdminRole
from models.appeal import BanAppeal
from services.notification_service import notify_user


async def notify_appeal_submitted(
    db: AsyncSession,
    *,
    appeal: BanAppeal,
) -> None:
    """신청자: 이의제기 접수 완료 알림"""
    await notify_user(
        db=db,
        user_id=appeal.user_id,
        type="system",
        title="이의제기 접수 완료",
        message="이의제기 신청이 접수되었습니다. 검토 후 결과를 알려드릴게요.",
        reference_type="appeal",
        reference_id=appeal.id,
    )


async def notify_appeal_result(
    db: AsyncSession,
    *,
    appeal: BanAppeal,
) -> None:
    """신청자: 이의제기 처리 결과 알림"""
    if appeal.status == "APPROVED":
        title = "이의제기 승인"
        message = "이의제기가 승인되어 제재가 해제되었습니다."
    else:
        title = "이의제기 거부"
        memo = f" ({appeal.admin_memo})" if appeal.admin_memo else ""
        message = f"이의제기가 거부되었습니다.{memo}"

    await notify_user(
        db=db,
        user_id=appeal.user_id,
        type="system",
        title=title,
        message=message,
        reference_type="appeal",
        reference_id=appeal.id,
    )


async def notify_admins_new_appeal(
    db: AsyncSession,
    *,
    appeal: BanAppeal,
    user_nickname: str,
) -> None:
    """관리자 전체: 이의제기 신청 접수 알림"""
    admin_ids = (
        await db.execute(select(AdminRole.user_id))
    ).scalars().all()

    for admin_id in admin_ids:
        await notify_user(
            db=db,
            user_id=admin_id,
            type="system",
            title="이의제기 신청 접수",
            message=f"{user_nickname} 님이 이의제기를 신청했습니다.",
            reference_type="appeal",
            reference_id=appeal.id,
        )
