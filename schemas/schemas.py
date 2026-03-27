import uuid
from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Optional


# ─── Auth ─────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    nickname: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6)
    phone: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    nickname: str
    role: str
    trust_score: float
    is_active: bool
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    nickname: str

    model_config = {"from_attributes": True}


# ─── Service ──────────────────────────────────────────────────────────────

class ServiceOut(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    max_members: int
    monthly_price: int
    logo_image_key: Optional[str]
    is_active: bool

    model_config = {"from_attributes": True}


# 프론트 /categories 호환용
class CategoryOut(BaseModel):
    category_id: uuid.UUID
    category_name: str

    model_config = {"from_attributes": True}


# ─── Party ────────────────────────────────────────────────────────────────

class PartyCreate(BaseModel):
    service_id: uuid.UUID
    title: str = Field(..., min_length=2, max_length=200)


class PartyOut(BaseModel):
    id: uuid.UUID
    leader_id: Optional[uuid.UUID]
    service_id: Optional[uuid.UUID]
    title: str
    status: Optional[str]          # ← PartyStatusEnum 대신 str로 변경
    host_nickname: Optional[str] = None
    service_name: Optional[str] = None
    category_name: Optional[str] = None
    max_members: Optional[int] = None
    monthly_price: Optional[int] = None
    logo_image_key: Optional[str] = None
    member_count: int = 0

    model_config = {"from_attributes": True}


class PartyListOut(BaseModel):
    parties: list[PartyOut]
    total: int
    page: int
    size: int


# ─── Notification ─────────────────────────────────────────────────────────

class NotificationOut(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    type: Optional[str]
    title: Optional[str]
    message: Optional[str]
    reference_type: Optional[str]
    reference_id: Optional[uuid.UUID]
    is_read: Optional[bool]
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ─── Common ───────────────────────────────────────────────────────────────

class MessageOut(BaseModel):
    message: str