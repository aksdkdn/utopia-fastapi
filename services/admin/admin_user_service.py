from fastapi import HTTPException, status
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from uuid import UUID

from models.user import User, UserReferrer
from models.admin import ActivityLog


async def update_admin_user_recommender_service(
    db: AsyncSession,
    *,
    target_user_id: UUID,
    referrer_nickname: str | None,
    admin_user_id: UUID,
    reason: str | None = None,
):
    target_result = await db.execute(
        select(User).where(User.id == target_user_id)
    )
    target_user = target_result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="대상 사용자를 찾을 수 없습니다.",
        )

    # 추천인 제거
    if not referrer_nickname or not referrer_nickname.strip():
        await db.execute(
            delete(UserReferrer).where(UserReferrer.user_id == target_user_id)
        )

        target_user.referrer_id = None
        target_user.referrer_count = 0

        db.add(
            ActivityLog(
                actor_user_id=admin_user_id,
                action_type="ADMIN_USER_REFERRER_UPDATE",
                description=f"관리자가 사용자({target_user.nickname})의 추천인을 제거했습니다.",
                target_id=target_user_id,
                extra_metadata={
                    "reason": reason,
                    "referrerNickname": None,
                },
            )
        )

        await db.commit()
        await db.refresh(target_user)
        return target_user

    nickname = referrer_nickname.strip()

    referrer_result = await db.execute(
        select(User).where(
            User.nickname == nickname,
            User.is_active.is_(True),
        )
    )
    referrer_user = referrer_result.scalar_one_or_none()

    if not referrer_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="존재하지 않는 추천인이거나 비활성화된 사용자입니다.",
        )

    if referrer_user.id == target_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="자기 자신은 추천인으로 설정할 수 없습니다.",
        )

    # 관리자 변경은 실무상 '교체'가 편함: 기존 추천인 전체 삭제 후 1명으로 재설정
    await db.execute(
        delete(UserReferrer).where(UserReferrer.user_id == target_user_id)
    )

    db.add(
        UserReferrer(
            user_id=target_user_id,
            referrer_id=referrer_user.id,
        )
    )

    target_user.referrer_id = referrer_user.id
    target_user.referrer_count = 1

    db.add(
        ActivityLog(
            actor_user_id=admin_user_id,
            action_type="ADMIN_USER_REFERRER_UPDATE",
            description=f"관리자가 사용자({target_user.nickname})의 추천인을 {referrer_user.nickname}(으)로 변경했습니다.",
            target_id=target_user_id,
            extra_metadata={
                "reason": reason,
                "referrerId": str(referrer_user.id),
                "referrerNickname": referrer_user.nickname,
            },
        )
    )

    await db.commit()
    await db.refresh(target_user)

    return target_user