import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import Optional
from core.database import get_db
from core.security import require_user
from models.party import Party, PartyMember, Service
from models.user import User
from schemas.party import PartyCreate, PartyOut, PartyListOut
from schemas.common import MessageOut
from schemas.service import ServiceOut, CategoryOut
router = APIRouter(prefix="/parties", tags=["parties"])


def _build_party_out(party: Party) -> PartyOut:
    return PartyOut(
        id=party.id,
        leader_id=party.leader_id,
        service_id=party.service_id,
        title=party.title,
        description=party.description,
        max_members=party.max_members,
        current_members=party.current_members,
        monthly_per_person=party.monthly_per_person,
        status=party.status,
        start_date=party.start_date,
        end_date=party.end_date,
        created_at=party.created_at,
        host_nickname=party.host.nickname if party.host else None,
        service_name=party.service.name if party.service else None,
        category_name=party.service.category if party.service else None,
        logo_image_key=party.service.logo_image_key if party.service else None,
    )


@router.get("/categories", response_model=list[CategoryOut], tags=["categories"])
async def list_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Service.category).distinct().order_by(Service.category)
    )
    categories = result.scalars().all()
    return [CategoryOut(category_id=c, category_name=c) for c in categories]


@router.get("/services", response_model=list[ServiceOut], tags=["services"])
async def list_services(
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(Service).where(Service.is_active == True).order_by(Service.name)  # noqa
    if category:
        q = q.where(Service.category == category)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("", response_model=PartyListOut)
async def list_parties(
    category: Optional[str] = Query(None),
    service_id: Optional[uuid.UUID] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(Party)
        .options(
            selectinload(Party.host),
            selectinload(Party.members),
            selectinload(Party.service),
        )
    )

    if service_id:
        q = q.where(Party.service_id == service_id)
    if category:
        q = q.join(Party.service).where(Service.category == category)
    if search:
        q = q.where(Party.title.ilike(f"%{search}%"))

    # 삭제되지 않은 활성 파티만
    q = q.where(Party.status != "canceled")

    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    q = q.offset((page - 1) * size).limit(size).order_by(Party.created_at.desc())
    result = await db.execute(q)
    parties = result.scalars().all()

    return PartyListOut(parties=[_build_party_out(p) for p in parties], total=total, page=page, size=size)


@router.get("/{party_id}", response_model=PartyOut)
async def get_party(party_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Party)
        .options(
            selectinload(Party.host),
            selectinload(Party.members),
            selectinload(Party.service),
        )
        .where(Party.id == party_id)
    )
    party = result.scalar_one_or_none()
    if not party:
        raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다.")
    return _build_party_out(party)


@router.post("", response_model=PartyOut, status_code=status.HTTP_201_CREATED)
async def create_party(
    body: PartyCreate,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    service = await db.get(Service, body.service_id)
    if not service or not service.is_active:
        raise HTTPException(status_code=400, detail="유효하지 않은 서비스입니다.")

    party = Party(
        leader_id=current_user.id,
        service_id=body.service_id,
        title=body.title,
        description=body.description,
        max_members=body.max_members,
        monthly_per_person=body.monthly_per_person,
        start_date=body.start_date,
        end_date=body.end_date,
        status="recruiting",
    )
    db.add(party)
    await db.commit()
    await db.refresh(party)

    result = await db.execute(
        select(Party)
        .options(selectinload(Party.host), selectinload(Party.members), selectinload(Party.service))
        .where(Party.id == party.id)
    )
    return _build_party_out(result.scalar_one())


@router.post("/{party_id}/join", response_model=MessageOut)
async def join_party(
    party_id: uuid.UUID,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Party)
        .options(selectinload(Party.members))
        .where(Party.id == party_id)
    )
    party = result.scalar_one_or_none()
    if not party:
        raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다.")
    if party.leader_id == current_user.id:
        raise HTTPException(status_code=400, detail="자신이 개설한 파티입니다.")
    if party.status != "recruiting":
        raise HTTPException(status_code=400, detail="모집 중인 파티가 아닙니다.")

    existing = await db.execute(
        select(PartyMember).where(
            PartyMember.party_id == party_id,
            PartyMember.user_id == current_user.id,
            PartyMember.status == "active",
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="이미 참여한 파티입니다.")

    # current_members 체크
    if party.current_members >= party.max_members:
        raise HTTPException(status_code=400, detail="파티 정원이 꽉 찼습니다.")

    db.add(PartyMember(party_id=party_id, user_id=current_user.id))

    # current_members 증가
    party.current_members += 1
    if party.current_members >= party.max_members:
        party.status = "full"

    await db.commit()
    return MessageOut(message="파티 참여가 완료되었습니다.")
