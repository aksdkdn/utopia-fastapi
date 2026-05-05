from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class AppealCreateIn(BaseModel):
    ban_type: str          # ip_ban / trust_score / manual
    ban_reference_id: Optional[str] = None
    reason: str


class AppealOut(BaseModel):
    id: str
    user_id: str
    ban_type: str
    ban_reference_id: Optional[str]
    reason: str
    status: str
    admin_memo: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


# 관리자용 - 제재 상세 포함
class AdminAppealOut(BaseModel):
    id: str
    user_id: str
    user_nickname: str
    user_email: str
    ban_type: str
    ban_reference_id: Optional[str]
    reason: str
    status: str
    admin_memo: Optional[str]
    reviewed_by_nickname: Optional[str]
    reviewed_at: Optional[str]
    created_at: str
    # 관련 제재 기록
    ban_detail: Optional[str]        # 제재 사유 텍스트
    ban_score_change: Optional[float]  # trust_score 변동량 (복구 시 사용)
    ban_created_at: Optional[str]


class AdminAppealReviewIn(BaseModel):
    status: str    # APPROVED / REJECTED
    admin_memo: Optional[str] = None
