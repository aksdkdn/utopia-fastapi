"""
정산 관련 라우터

POST   /api/settlement/parties/{party_id}/request   방장 정산 승인 요청
GET    /api/settlement/parties/{party_id}/status    현재 정산 상태 조회
GET    /api/settlement/parties/{party_id}/notice    공지 조회
POST   /api/settlement/parties/{party_id}/notice    공지 등록 (방장)
PUT    /api/settlement/parties/{party_id}/notice    공지 수정 (방장)
DELETE /api/settlement/parties/{party_id}/notice    공지 삭제 (방장)
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.security import require_user
from models.admin import Settlement
from models.party import Party, PartyMember, PartyNotice, PartyChat
from models.payment import Payment
from models.user import User
from services.notification_service import notify_user

router = APIRouter(prefix="/settlement", tags=["settlement"])


async def _get_party_and_check_leader(
    party_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> Party:
    party = await db.get(Party, party_id)
    if not party:
        raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다.")
    if party.leader_id != current_user.id:
        raise HTTPException(status_code=403, detail="방장만 가능합니다.")
    return party


async def _get_active_member_ids(db: AsyncSession, party: Party) -> list[uuid.UUID]:
    result = await db.execute(
        select(PartyMember.user_id).where(
            PartyMember.party_id == party.id,
            PartyMember.status == "active",
        )
    )
    member_ids = [row[0] for row in result.all()]
    if party.leader_id not in member_ids:
        member_ids.append(party.leader_id)
    return member_ids


async def _check_all_paid(
    db: AsyncSession,
    party: Party,
    member_ids: list[uuid.UUID],
    billing_month: str,
) -> tuple[bool, list[uuid.UUID]]:
    result = await db.execute(
        select(Payment.user_id).where(
            Payment.party_id == party.id,
            Payment.billing_month == billing_month,
            Payment.status == "approved",
        )
    )
    paid_ids = {row[0] for row in result.all()}
    unpaid = [uid for uid in member_ids if uid not in paid_ids]
    return len(unpaid) == 0, unpaid


# ── 정산 승인 요청 ──────────────────────────────────────────

@router.post("/parties/{party_id}/request")
async def request_settlement(
    party_id: uuid.UUID,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    party = await _get_party_and_check_leader(party_id, current_user, db)
    billing_month = datetime.now(timezone.utc).strftime("%Y-%m")

    existing = await db.execute(
        select(Settlement).where(
            Settlement.party_id == party_id,
            Settlement.billing_month == billing_month,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="이미 이번 달 정산 요청이 존재합니다.")

    member_ids = await _get_active_member_ids(db, party)
    all_paid, unpaid_ids = await _check_all_paid(db, party, member_ids, billing_month)

    paid_result = await db.execute(
        select(Payment).where(
            Payment.party_id == party_id,
            Payment.billing_month == billing_month,
            Payment.status == "approved",
        )
    )
    payments = paid_result.scalars().all()
    total_amount = sum(p.amount for p in payments)

    if all_paid:
        settlement = Settlement(
            party_id=party_id,
            leader_id=party.leader_id,
            total_amount=total_amount,
            member_count=len(member_ids),
            billing_month=billing_month,
            status="approved",
            approved_at=datetime.now(timezone.utc),
        )
        db.add(settlement)
        party.status = "active"
        await db.commit()
        await db.refresh(settlement)

        await notify_user(
            db=db,
            user_id=party.leader_id,
            type="settlement",
            title="정산 승인 완료",
            message=f"[{party.title}] 모든 멤버 결제가 확인되어 정산이 승인되었습니다. 아이디/비밀번호를 공유해주세요.",
            reference_type="settlement",
            reference_id=party.id,
            metadata={
                "event_code": "SETTLEMENT_AUTO_APPROVED",
                "party_id": str(party.id),
                "settlement_id": str(settlement.id),
            },
        )

        try:
            from routers.chat import manager
            await manager.broadcast(str(party_id), {
                "type": "settlement_approved",
                "party_id": str(party_id),
                "settlement_id": str(settlement.id),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass

        return {
            "status": "approved",
            "settlement_id": str(settlement.id),
            "message": "모든 멤버 결제 완료. 정산이 자동 승인되었습니다.",
        }
    else:
        settlement = Settlement(
            party_id=party_id,
            leader_id=party.leader_id,
            total_amount=total_amount,
            member_count=len(member_ids),
            billing_month=billing_month,
            status="pending",
        )
        db.add(settlement)
        await db.commit()
        await db.refresh(settlement)

        return {
            "status": "pending",
            "settlement_id": str(settlement.id),
            "unpaid_count": len(unpaid_ids),
            "message": f"미결제 멤버 {len(unpaid_ids)}명이 있어 관리자 검토 후 처리됩니다.",
        }


# ── 정산 상태 조회 ──────────────────────────────────────────

@router.get("/parties/{party_id}/status")
async def get_settlement_status(
    party_id: uuid.UUID,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    billing_month = datetime.now(timezone.utc).strftime("%Y-%m")
    result = await db.execute(
        select(Settlement).where(
            Settlement.party_id == party_id,
            Settlement.billing_month == billing_month,
        )
    )
    settlement = result.scalar_one_or_none()
    if not settlement:
        return {"status": None, "settlement_id": None}

    return {
        "status": settlement.status,
        "settlement_id": str(settlement.id),
        "approved_at": settlement.approved_at.isoformat() if settlement.approved_at else None,
    }


# ── 채팅방 공지 CRUD ────────────────────────────────────────

class NoticeUpsertRequest(BaseModel):
    content: str


@router.get("/parties/{party_id}/notice")
async def get_notice(
    party_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PartyNotice).where(PartyNotice.party_id == party_id)
    )
    notice = result.scalar_one_or_none()
    if not notice:
        return {"notice": None}
    return {
        "notice": {
            "id": str(notice.id),
            "content": notice.content,
            "created_by": str(notice.created_by),
            "updated_at": notice.updated_at.isoformat(),
        }
    }


@router.post("/parties/{party_id}/notice")
async def create_notice(
    party_id: uuid.UUID,
    body: NoticeUpsertRequest,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    party = await _get_party_and_check_leader(party_id, current_user, db)

    existing = await db.execute(
        select(PartyNotice).where(PartyNotice.party_id == party_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="이미 공지가 있습니다. PUT으로 수정하세요.")

    notice = PartyNotice(
        party_id=party_id,
        created_by=current_user.id,
        content=body.content,
    )
    db.add(notice)

    sys_chat = PartyChat(
        party_id=party_id,
        sender_id=current_user.id,
        message=f"[공지] {body.content}",
        message_type="notice",
    )
    db.add(sys_chat)
    await db.commit()
    await db.refresh(notice)

    try:
        from routers.chat import manager
        await manager.broadcast(str(party_id), {
            "type": "notice_updated",
            "notice": {
                "id": str(notice.id),
                "content": notice.content,
                "updated_at": notice.updated_at.isoformat(),
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass

    return {"notice": {"id": str(notice.id), "content": notice.content}}


@router.put("/parties/{party_id}/notice")
async def update_notice(
    party_id: uuid.UUID,
    body: NoticeUpsertRequest,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_party_and_check_leader(party_id, current_user, db)

    result = await db.execute(
        select(PartyNotice).where(PartyNotice.party_id == party_id)
    )
    notice = result.scalar_one_or_none()
    if not notice:
        raise HTTPException(status_code=404, detail="공지가 없습니다. POST로 먼저 생성하세요.")

    notice.content = body.content
    notice.updated_at = datetime.now(timezone.utc)
    await db.commit()

    try:
        from routers.chat import manager
        await manager.broadcast(str(party_id), {
            "type": "notice_updated",
            "notice": {
                "id": str(notice.id),
                "content": notice.content,
                "updated_at": notice.updated_at.isoformat(),
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass

    return {"notice": {"id": str(notice.id), "content": notice.content}}


@router.delete("/parties/{party_id}/notice")
async def delete_notice(
    party_id: uuid.UUID,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_party_and_check_leader(party_id, current_user, db)

    result = await db.execute(
        select(PartyNotice).where(PartyNotice.party_id == party_id)
    )
    notice = result.scalar_one_or_none()
    if not notice:
        raise HTTPException(status_code=404, detail="공지가 없습니다.")

    await db.delete(notice)
    await db.commit()

    try:
        from routers.chat import manager
        await manager.broadcast(str(party_id), {
            "type": "notice_deleted",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass

    return {"message": "공지가 삭제되었습니다."}
