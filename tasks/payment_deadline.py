"""
결제 마감일(payment_deadline) 관련 태스크

1. check_payment_deadline  : Celery beat 매 10분 실행
   - 마감일 지난 미결제 멤버 → 강퇴 + 신뢰도 -5
   - 강퇴 후 인원 미달이면 payment_deadline = NULL, status = recruiting
   - 아직 꽉 찼으면 3일 다시 부여

2. sync_payment_deadline : parties 라우터에서 인원 변동 시 직접 await 호출
   - 인원 꽉 찼으면 payment_deadline = now + 3일 (기존 deadline 없을 때만)
   - 인원 미달이면 payment_deadline = NULL, status = recruiting
"""

import asyncio
import uuid
from datetime import datetime, timezone, timedelta

from core.celery_app import celery_app
from core.database import AsyncSessionLocal
from models.party import Party, PartyMember
from models.payment import Payment
from models.user import User
from models.mypage.trust_score import TrustScore
from sqlalchemy import select

NOSHOW_PENALTY = -5.0
PAYMENT_DEADLINE_DAYS = 3


async def _apply_trust_penalty(db, user: User, delta: float, reason: str) -> None:
    previous = float(user.trust_score) if user.trust_score is not None else 36.5
    new_score = max(0.0, round(previous + delta, 1))
    change = round(new_score - previous, 1)
    if change == 0:
        return
    user.trust_score = new_score
    db.add(TrustScore(
        user_id=user.id,
        previous_score=previous,
        new_score=new_score,
        change_amount=change,
        reason=reason,
        created_by=user.id,
    ))


async def _is_paid(db, user_id: uuid.UUID, party_id: uuid.UUID, billing_month: str) -> bool:
    result = await db.execute(
        select(Payment).where(
            Payment.user_id == user_id,
            Payment.party_id == party_id,
            Payment.billing_month == billing_month,
            Payment.status == "approved",
        )
    )
    return result.scalar_one_or_none() is not None


async def _run_check_payment_deadline() -> dict:
    now = datetime.now(timezone.utc)
    billing_month = now.strftime("%Y-%m")
    kicked_count = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Party).where(
                Party.payment_deadline.isnot(None),
                Party.payment_deadline <= now,
                Party.status != "ended",
            )
        )
        parties = result.scalars().all()

        for party in parties:
            kicked_any = False

            # active 멤버(방장 제외) 미결제자 강퇴
            members_result = await db.execute(
                select(PartyMember).where(
                    PartyMember.party_id == party.id,
                    PartyMember.status == "active",
                    PartyMember.user_id != party.leader_id,
                )
            )
            members = members_result.scalars().all()

            for member in members:
                if not await _is_paid(db, member.user_id, party.id, billing_month):
                    member.status = "left"
                    member.left_at = now
                    party.current_members = max(0, (party.current_members or 1) - 1)
                    user_result = await db.execute(select(User).where(User.id == member.user_id))
                    user = user_result.scalar_one_or_none()
                    if user:
                        await _apply_trust_penalty(db, user, NOSHOW_PENALTY, f"노쇼(결제미완료) - 파티:{party.id}")
                    kicked_any = True
                    kicked_count += 1

            # 방장 미결제 감점 (강퇴는 하지 않음)
            if not await _is_paid(db, party.leader_id, party.id, billing_month):
                leader_result = await db.execute(select(User).where(User.id == party.leader_id))
                leader = leader_result.scalar_one_or_none()
                if leader:
                    await _apply_trust_penalty(db, leader, NOSHOW_PENALTY, f"노쇼(결제미완료,방장) - 파티:{party.id}")
                kicked_any = True

            if kicked_any:
                max_m = party.max_members or 0
                current = party.current_members or 0
                if current < max_m:
                    party.payment_deadline = None
                    party.status = "recruiting"
                else:
                    # 여전히 꽉 찼으면 3일 재부여
                    party.payment_deadline = now + timedelta(days=PAYMENT_DEADLINE_DAYS)

        await db.commit()

    return {"kicked_count": kicked_count, "run_at": now.isoformat()}


@celery_app.task(
    name="tasks.payment_deadline.check_payment_deadline",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def check_payment_deadline(self):
    try:
        return asyncio.run(_run_check_payment_deadline())
    except Exception as exc:
        raise self.retry(exc=exc)


async def sync_payment_deadline(db, party: Party) -> None:
    """
    인원 변동 시마다 호출.
    꽉 찼으면 deadline 신규 설정(기존 있으면 유지), 미달이면 초기화.
    """
    max_m = party.max_members or 0
    current = party.current_members or 0

    if max_m > 0 and current >= max_m:
        if party.payment_deadline is None:
            party.payment_deadline = datetime.now(timezone.utc) + timedelta(days=PAYMENT_DEADLINE_DAYS)
            party.status = "full"
    else:
        party.payment_deadline = None
        if party.status == "full":
            party.status = "recruiting"
