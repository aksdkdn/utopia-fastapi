import json
import asyncio
import uuid
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.dialects.postgresql import insert as pg_insert
import redis.asyncio as aioredis
from jose import JWTError, jwt
from core.config import settings
from core.database import get_db, AsyncSessionLocal
from core.security import get_current_user_optional
from models.party import Party, PartyMember, PartyChat, ChatReadStatus, Service
from models.payment import Payment
from models.user import User
from models.refresh_token import RefreshToken
from services.mypage.profile_service import _build_profile_image_url

router = APIRouter(prefix="/chat", tags=["chat"])

redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

REDIS_TTL = 60 * 60 * 24 * 3
OLLAMA_URL = settings.OLLAMA_URL
OLLAMA_MODEL = settings.OLLAMA_MODEL
ML_SERVER_URL = settings.ML_SERVER_URL

# 2단계 ML 레이블 한국어 매핑
LABEL_KO = {
    "hate": "혐오/심한 욕설",
    "offensive": "부적절한 표현",
}


def warn_key(party_id: str, user_id: str) -> str:
    return f"warn:{party_id}:{user_id}"

def redis_msg_key(party_id: str) -> str:
    return f"chat:party:{party_id}:messages"

def blocked_key(user_id: str) -> str:
    return f"blocked:user:{user_id}"


def _party_max_members(party: Party, service: Service | None) -> int | None:
    if party.max_members is not None:
        return party.max_members
    if service is not None:
        return service.max_members
    return None


def _party_member_count(party: Party, members: list[dict]) -> int:
    return len(members)


def _party_total_price(party: Party, service: Service | None) -> int | None:
    if service is not None:
        return service.original_price or service.monthly_price
    max_members = _party_max_members(party, service)
    if party.monthly_per_person is not None and max_members:
        return party.monthly_per_person * max_members
    return None


def _safe_profile_image_url(profile_image_key: str | None) -> str | None:
    try:
        return _build_profile_image_url(profile_image_key)
    except Exception:
        return None


def _serialize_message(chat: PartyChat, sender: User | None, unread_count: int = 0) -> dict:
    return {
        "type": "message",
        "chat_id": str(chat.id),
        "party_id": str(chat.party_id),
        "user_id": str(chat.sender_id) if chat.sender_id else None,
        "nickname": sender.nickname if sender else None,
        "profile_image": _safe_profile_image_url(sender.profile_image_key) if sender else None,
        "content": chat.message,
        "created_at": chat.created_at.isoformat(),
        "unread_count": unread_count,
    }


def _serialize_member(
    user: User,
    *,
    role: str,
    status: str,
    joined_at: datetime | None,
    payment_status: str | None = None,
) -> dict:
    return {
        "user_id": str(user.id),
        "nickname": user.nickname,
        "name": (user.name or user.nickname),
        "role": role,
        "status": status,
        "trust_score": float(user.trust_score) if user.trust_score is not None else None,
        "joined_at": joined_at.isoformat() if joined_at else None,
        "profile_image": _safe_profile_image_url(user.profile_image_key),
        "payment_status": payment_status,
        "is_active": bool(user.is_active),
    }

def _get_user_id_from_ws_cookie(ws: WebSocket) -> str | None:
    access_token = ws.cookies.get("access_token")
    if not access_token:
        return None
    try:
        payload = jwt.decode(
            access_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        if payload.get("type") != "access":
            return None
        user_id_str = payload.get("sub", "")
        uuid.UUID(user_id_str)
        return user_id_str
    except (JWTError, ValueError):
        return None


# ── 파티 멤버 수 헬퍼 ────────────────────────────────────────

async def _get_total_member_count(party_id: str) -> int:
    """파티 전체 인원 수 반환 (리더 포함)"""
    try:
        async with AsyncSessionLocal() as db:
            party_r = await db.execute(select(Party).where(Party.id == uuid.UUID(party_id)))
            party_obj = party_r.scalar_one_or_none()
            cnt_result = await db.execute(
                select(func.count()).select_from(PartyMember).where(
                    PartyMember.party_id == uuid.UUID(party_id),
                    PartyMember.status == "active",
                )
            )
            active_cnt = cnt_result.scalar() or 0
            if party_obj:
                ldr_check = await db.execute(
                    select(PartyMember).where(
                        PartyMember.party_id == uuid.UUID(party_id),
                        PartyMember.user_id == party_obj.leader_id,
                        PartyMember.status == "active",
                    )
                )
                ldr_in = ldr_check.scalar_one_or_none() is not None
                return active_cnt if ldr_in else active_cnt + 1
            return active_cnt
    except Exception:
        return 1


# ── 3단계 탐지 파이프라인 ────────────────────────────────────

async def check_message(content: str) -> dict:
    from routers.admin_moderation_config import get_config
    config = await get_config()
    stripped = content.strip()

    if config.get("stage1_enabled", True):
        whitelist = config.get("whitelist", [])
        blacklist = config.get("blacklist", [])

        has_blacklist = any(w in stripped for w in blacklist)
        has_whitelist = any(w in stripped for w in whitelist)

        if has_blacklist:
            return {"violation": True, "severe": True, "reason": "욕설 축약어", "stage": 1, "score": None}
        if has_whitelist:
            return {"violation": False, "severe": False, "reason": "", "stage": 1, "score": None}

    if config.get("stage2_enabled", True) and ML_SERVER_URL:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.post(ML_SERVER_URL, json={"content": stripped})
                ml = resp.json()
                label = ml["label"]
                score = ml["score"]
                pass_t = config.get("stage2_pass_threshold", 0.75)
                block_t = config.get("stage2_block_threshold", 0.97)

                if label == "none" and score >= pass_t:
                    return {"violation": False, "severe": False, "reason": "", "stage": 2, "score": score}
                if label != "none" and score >= block_t:
                    return {
                        "violation": True,
                        "severe": label == "hate",
                        "reason": LABEL_KO.get(label, label),
                        "stage": 2,
                        "score": score,
                    }
        except Exception:
            pass

    if config.get("stage3_enabled", True):
        return await _check_message_ollama(content, config)

    return {"violation": False, "severe": False, "reason": "", "stage": 0, "score": None}


async def _check_message_ollama(content: str, config: dict) -> dict:
    examples = config.get("ollama_prompt_examples", [])
    none_ex = [e["text"] for e in examples if e["label"] == "none"]
    offensive_ex = [e["text"] for e in examples if e["label"] == "offensive"]

    none_str = ", ".join(f'"{t}"' for t in none_ex) if none_ex else '"ㅇㅇ", "ㅎㅇ"'
    off_str = ", ".join(f'"{t}"' for t in offensive_ex) if offensive_ex else '"ㅅㅂ", "존나"'

    prompt = f"""당신은 한국어 채팅 욕설 탐지 전문가입니다.
아래 예시를 참고해 판단하세요.

[위반 아님 - none]: {none_str}
[경고 수준 - offensive]: {off_str}
[즉시 차단 - hate]: 특정 대상 혐오, 심한 욕설 조합

메시지: "{content}"

JSON만 응답, 다른 텍스트 금지:
{{"violation": true/false, "severe": true/false, "reason": "한 줄 이유"}}"""

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
            )
            text = resp.json()["message"]["content"].strip()
            if "```" in text:
                text = text.split("```")[1].replace("json", "").strip()
            parsed = json.loads(text)
            return {
                "violation": parsed.get("violation", False),
                "severe": parsed.get("severe", False),
                "reason": parsed.get("reason", ""),
                "stage": 3,
                "score": None,
            }
    except Exception:
        return {"violation": False, "severe": False, "reason": "", "stage": 3, "score": None}


class ConnectionManager:
    def __init__(self):
        self.active: dict[str, list[WebSocket]] = {}
        # party_id -> {user_id: ws}
        self.user_ws: dict[str, dict[str, WebSocket]] = {}

    async def connect(self, party_id: str, ws: WebSocket, user_id: str = "guest"):
        await ws.accept()
        self.active.setdefault(party_id, []).append(ws)
        self.user_ws.setdefault(party_id, {})[user_id] = ws

    def disconnect(self, party_id: str, ws: WebSocket, user_id: str = "guest"):
        if party_id in self.active:
            try:
                self.active[party_id].remove(ws)
            except ValueError:
                pass
        if party_id in self.user_ws:
            self.user_ws[party_id].pop(user_id, None)

    def get_online_user_ids(self, party_id: str) -> set[str]:
        return set(self.user_ws.get(party_id, {}).keys())

    async def broadcast(self, party_id: str, message: dict):
        msg_str = json.dumps(message, ensure_ascii=False)
        if party_id in self.active:
            for ws in list(self.active[party_id]):
                try:
                    await ws.send_text(msg_str)
                except Exception:
                    self.disconnect(party_id, ws)

    async def send_personal(self, ws: WebSocket, message: dict):
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False))
        except Exception:
            pass


manager = ConnectionManager()


async def delete_message_from_redis(party_id: str, content: str) -> bool:
    key = redis_msg_key(party_id)
    messages = await redis_client.lrange(key, 0, -1)
    for raw in reversed(messages):
        try:
            parsed = json.loads(raw)
            if parsed.get("content") == content and parsed.get("type") == "message":
                await redis_client.lrem(key, -1, raw)
                return True
        except Exception:
            continue
    return False


async def delete_message_from_db(party_id: str, user_id: str, content: str):
    try:
        sender_uuid = uuid.UUID(user_id)
        party_uuid = uuid.UUID(party_id)
    except (ValueError, TypeError):
        return
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PartyChat)
                .where(
                    PartyChat.party_id == party_uuid,
                    PartyChat.sender_id == sender_uuid,
                    PartyChat.message == content,
                    PartyChat.is_deleted == False,
                )
                .order_by(PartyChat.created_at.desc())
                .limit(1)
            )
            chat = result.scalar_one_or_none()
            if chat:
                chat.is_deleted = True
                await db.commit()
    except Exception as e:
        print(f"[DB DELETE ERROR] {e}")


async def _flag_chat_in_db(
    party_id: str,
    user_id: str,
    content: str,
    reason: str,
    moderation_status: str,
    stage: int = 0,
    score: float | None = None,
) -> None:
    try:
        sender_uuid = uuid.UUID(user_id)
        party_uuid = uuid.UUID(party_id)
    except (ValueError, TypeError):
        return
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PartyChat)
                .where(
                    PartyChat.party_id == party_uuid,
                    PartyChat.sender_id == sender_uuid,
                    PartyChat.message == content,
                )
                .order_by(PartyChat.created_at.desc())
                .limit(1)
            )
            chat = result.scalar_one_or_none()
            if chat:
                chat.is_flagged = True
                chat.flag_reason = reason
                chat.flag_confidence = score
                chat.flag_stage = stage
                chat.moderation_status = moderation_status
                await db.commit()
    except Exception as e:
        print(f"[FLAG DB ERROR] {e}")


async def _ban_user_in_db(party_id: str, user_id: str) -> None:
    try:
        party_uuid = uuid.UUID(party_id)
        user_uuid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        return
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(PartyMember)
                .where(
                    PartyMember.party_id == party_uuid,
                    PartyMember.user_id == user_uuid,
                )
                .values(status="banned")
            )
            party_result = await db.execute(
                select(Party).where(Party.id == party_uuid)
            )
            party = party_result.scalar_one_or_none()
            if party and party.current_members:
                party.current_members = max(0, party.current_members - 1)
            await db.commit()
    except Exception as e:
        print(f"[BAN DB ERROR] {e}")


async def _apply_trust_penalty(user_id: str, delta: float, reason: str) -> float:
    try:
        from models.mypage.trust_score import TrustScore
        user_uuid = uuid.UUID(user_id)
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id == user_uuid))
            user = result.scalar_one_or_none()
            if not user:
                return 36.5
            previous = float(user.trust_score) if user.trust_score is not None else 36.5
            new_score = max(0.0, round(previous + delta, 1))
            user.trust_score = new_score
            db.add(TrustScore(
                user_id=user_uuid,
                previous_score=previous,
                new_score=new_score,
                change_amount=round(new_score - previous, 1),
                reason=reason,
                created_by=user_uuid,
            ))
            await db.commit()
            return new_score
    except Exception as e:
        print(f"[TRUST PENALTY ERROR] {e}")
        return 36.5


async def _increment_chat_warn_count(user_id: str) -> int:
    try:
        user_uuid = uuid.UUID(user_id)
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id == user_uuid))
            user = result.scalar_one_or_none()
            if not user:
                return 0
            user.chat_warn_count = (user.chat_warn_count or 0) + 1
            await db.commit()
            return user.chat_warn_count
    except Exception as e:
        print(f"[WARN COUNT ERROR] {e}")
        return 0


async def _apply_status_by_score(user_id: str, score: float, warn_count: int) -> None:
    try:
        user_uuid = uuid.UUID(user_id)
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id == user_uuid))
            user = result.scalar_one_or_none()
            if not user:
                return
            if score <= 0 or warn_count >= 4:
                user.is_active = False
                user.banned_until = None
            elif score < 10 or warn_count >= 3:
                user.is_active = False
                user.banned_until = datetime.now(timezone.utc) + timedelta(days=30)
            elif score < 20:
                user.banned_until = datetime.now(timezone.utc) + timedelta(hours=72)
            await db.commit()
    except Exception as e:
        print(f"[STATUS BY SCORE ERROR] {e}")


async def _ban_user_ip(user_id: str) -> None:
    try:
        from services.notification_ws_service import notification_connection_manager
        user_uuid = uuid.UUID(user_id)
        async with AsyncSessionLocal() as db:
            ip_result = await db.execute(
                select(RefreshToken.ip_address)
                .where(
                    RefreshToken.user_id == user_uuid,
                    RefreshToken.ip_address != None,
                )
                .order_by(RefreshToken.created_at.desc())
                .limit(1)
            )
            ip = ip_result.scalar_one_or_none()
            if not ip:
                return

            await redis_client.set(f"ip:banned:{ip}", "1")

            token_rows = await db.execute(
                select(RefreshToken.user_id)
                .where(
                    RefreshToken.ip_address == ip,
                    RefreshToken.revoked_at == None,
                    RefreshToken.expires_at > datetime.now(timezone.utc),
                )
                .distinct()
            )
            affected_user_ids = {str(row[0]) for row in token_rows.all()}
            affected_user_ids.add(user_id)

            for uid_str in affected_user_ids:
                try:
                    uid = uuid.UUID(uid_str)
                    await db.execute(
                        RefreshToken.__table__.delete().where(RefreshToken.user_id == uid)
                    )
                    await notification_connection_manager.send_to_user(uid, {
                        "type": "ip_banned",
                        "content": "같은 IP 사용자의 규정 위반으로 접속이 차단되었습니다.",
                    })
                except Exception as e:
                    print(f"[BAN IP USER ERROR] uid={uid_str} {e}")

            await db.commit()
    except Exception as e:
        print(f"[BAN IP ERROR] {e}")


# ── 모더레이션 메인 함수 ────────────────────────────────────

async def moderate_in_background(party_id: str, user_id: str, content: str, ws: WebSocket):
    moderation = await check_message(content)

    if moderation["severe"]:
        await redis_client.set(blocked_key(user_id), "1", ex=REDIS_TTL)
        await delete_message_from_redis(party_id, content)
        await delete_message_from_db(party_id, user_id, content)
        await _ban_user_in_db(party_id, user_id)
        new_score = await _apply_trust_penalty(user_id, -5.0, f"심한 욕설 감지: {moderation['reason']}")
        total_warn = await _increment_chat_warn_count(user_id)
        wk = warn_key(party_id, user_id)
        await redis_client.incr(wk)
        await redis_client.expire(wk, REDIS_TTL)
        await _flag_chat_in_db(
            party_id, user_id, content,
            moderation["reason"], "blocked",
            stage=moderation["stage"], score=moderation["score"],
        )
        await _apply_status_by_score(user_id, new_score, total_warn)
        await _ban_user_ip(user_id)
        await manager.send_personal(ws, {
            "type": "error",
            "content": f"심각한 욕설이 감지되어 차단되었습니다. ({moderation['reason']})",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await manager.send_personal(ws, {
            "type": "force_logout",
            "content": "심각한 위반으로 계정이 정지되었습니다.",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await manager.broadcast(party_id, {
            "type": "message_deleted",
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await manager.broadcast(party_id, {
            "type": "system",
            "content": "부적절한 메시지가 삭제되었습니다.",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    elif moderation["violation"]:
        wk = warn_key(party_id, user_id)
        party_warn = int(await redis_client.incr(wk))
        await redis_client.expire(wk, REDIS_TTL)
        new_score = await _apply_trust_penalty(user_id, -1.0, f"욕설 감지: {moderation['reason']}")
        total_warn = await _increment_chat_warn_count(user_id)
        await _flag_chat_in_db(
            party_id, user_id, content,
            moderation["reason"], "warned",
            stage=moderation["stage"], score=moderation["score"],
        )
        await _apply_status_by_score(user_id, new_score, total_warn)
        if party_warn >= 3:
            await redis_client.set(blocked_key(user_id), "1", ex=REDIS_TTL)
            await _ban_user_in_db(party_id, user_id)
            await _ban_user_ip(user_id)
            await manager.send_personal(ws, {
                "type": "error",
                "content": f"경고 {party_warn}회 누적으로 채팅이 차단되었습니다.",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            await manager.send_personal(ws, {
                "type": "force_logout",
                "content": "경고 누적으로 계정이 정지되었습니다.",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        else:
            await manager.send_personal(ws, {
                "type": "warning",
                "content": f"경고 {party_warn}/3회: 부적절한 표현이 감지되었습니다. (신뢰도 -1점)",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })


# ── 일반 API 엔드포인트 ──────────────────────────────────────

from sqlalchemy import func

@router.get("/parties/{party_id}/messages")
async def get_messages(party_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PartyChat, User)
        .outerjoin(User, PartyChat.sender_id == User.id)
        .where(PartyChat.party_id == party_id, PartyChat.is_deleted == False)
        .order_by(PartyChat.created_at.desc())
        .limit(100)
    )
    rows = result.all()

    # 파티 전체 멤버 수 (리더 포함)
    party_result = await db.execute(select(Party).where(Party.id == party_id))
    party = party_result.scalar_one_or_none()
    member_count_result = await db.execute(
        select(func.count()).select_from(PartyMember).where(
            PartyMember.party_id == party_id,
            PartyMember.status == "active",
        )
    )
    active_member_count = member_count_result.scalar() or 0
    if party:
        ldr_check = await db.execute(
            select(PartyMember).where(
                PartyMember.party_id == party_id,
                PartyMember.user_id == party.leader_id,
                PartyMember.status == "active",
            )
        )
        ldr_in = ldr_check.scalar_one_or_none() is not None
        total_members = active_member_count if ldr_in else active_member_count + 1
    else:
        total_members = active_member_count

    chat_ids = [chat.id for chat, _ in rows]
    unread_map: dict[uuid.UUID, int] = {}
    if chat_ids:
        read_counts = await db.execute(
            select(ChatReadStatus.chat_id, func.count().label("cnt"))
            .where(ChatReadStatus.chat_id.in_(chat_ids))
            .group_by(ChatReadStatus.chat_id)
        )
        read_map = {row.chat_id: row.cnt for row in read_counts}
        for chat_id in chat_ids:
            unread_map[chat_id] = max(0, total_members - read_map.get(chat_id, 0))

    return [
        _serialize_message(chat, sender, unread_map.get(chat.id, 0))
        for chat, sender in reversed(rows)
    ]


@router.get("/parties/{party_id}/info")
async def get_party_info(
    party_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    party_result = await db.execute(
        select(Party)
        .options(selectinload(Party.host), selectinload(Party.service))
        .where(Party.id == party_id)
    )
    party = party_result.scalar_one_or_none()
    if not party:
        raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다.")

    result = await db.execute(
        select(PartyMember, User)
        .join(User, PartyMember.user_id == User.id)
        .where(PartyMember.party_id == party_id, PartyMember.status == "active")
        .order_by(PartyMember.joined_at.asc())
    )
    rows = result.all()
    billing_month = datetime.now(timezone.utc).strftime("%Y-%m")
    member_user_ids = [user.id for _, user in rows]
    if party.host:
        member_user_ids.append(party.host.id)
    unique_member_user_ids = list({user_id for user_id in member_user_ids})

    paid_user_ids: set[uuid.UUID] = set()
    if unique_member_user_ids:
        paid_result = await db.execute(
            select(Payment.user_id).where(
                Payment.party_id == party_id,
                Payment.billing_month == billing_month,
                Payment.status == "approved",
                Payment.user_id.in_(unique_member_user_ids),
            )
        )
        paid_user_ids = set(paid_result.scalars().all())

    members = [
        _serialize_member(
            user,
            role=member.role,
            status=member.status,
            joined_at=member.joined_at,
            payment_status="completed" if user.id in paid_user_ids else "pending",
        )
        for member, user in rows
    ]

    if party.host and not any(member["user_id"] == str(party.host.id) for member in members):
        members.insert(
            0,
            _serialize_member(
                party.host,
                role="leader",
                status="active",
                joined_at=party.created_at,
                payment_status="completed" if party.host.id in paid_user_ids else "pending",
            ),
        )

    members.sort(
        key=lambda member: (
            0 if member["role"] == "leader" else 1,
            member["joined_at"] or "",
        )
    )

    service = party.service

    is_leader = False
    has_referrer_discount = False
    if current_user:
        is_leader = party.leader_id == current_user.id
        if current_user.referrer_id is not None:
            member_user_ids = {m["user_id"] for m in members}
            has_referrer_discount = (
                str(current_user.referrer_id) in member_user_ids
            )

    return {
        "party_id": str(party.id),
        "title": party.title,
        "status": party.status.lower() if party.status else None,
        "max_members": _party_max_members(party, service),
        "member_count": _party_member_count(party, members),
        "monthly_price": _party_total_price(party, service),
        "monthly_per_person": party.monthly_per_person,
        "quick_match_fee_rate": float(service.quick_match_fee_rate) if service and service.quick_match_fee_rate is not None else 0.0,
        "leader_discount_rate": float(service.leader_discount_rate) if service and service.leader_discount_rate is not None else None,
        "referral_discount_rate": float(service.referral_discount_rate) if service and service.referral_discount_rate is not None else None,
        "is_leader": is_leader,
        "has_referrer_discount": has_referrer_discount,
        "start_date": party.start_date.isoformat() if party.start_date else None,
        "end_date": party.end_date.isoformat() if party.end_date else None,
        "category_name": service.category if service else None,
        "service_name": service.name if service else None,
        "host_nickname": party.host.nickname if party.host else None,
        "members": members,
    }


# ── 웹소켓 메인 핸들러 ────────────────────────────────────────

@router.websocket("/ws/{party_id}")
async def websocket_chat(
    party_id: str,
    ws: WebSocket,
    nickname: str = Query(default="익명"),
):
    jwt_user_id = _get_user_id_from_ws_cookie(ws)
    safe_user_id = jwt_user_id if jwt_user_id else "guest"

    # 멤버 검증
    if safe_user_id != "guest":
        try:
            party_uuid = uuid.UUID(party_id)
            user_uuid = uuid.UUID(safe_user_id)
            async with AsyncSessionLocal() as db:
                party_result = await db.execute(
                    select(Party).where(Party.id == party_uuid)
                )
                party = party_result.scalar_one_or_none()
                if not party:
                    await ws.close(code=4004)
                    return

                is_leader = party.leader_id == user_uuid
                if not is_leader:
                    member_result = await db.execute(
                        select(PartyMember).where(
                            PartyMember.party_id == party_uuid,
                            PartyMember.user_id == user_uuid,
                            PartyMember.status == "active",
                        )
                    )
                    if not member_result.scalar_one_or_none():
                        await ws.close(code=4003)
                        return
        except Exception as e:
            print(f"[WS AUTH ERROR] {e}")
            await ws.close(code=4000)
            return

    await manager.connect(party_id, ws, safe_user_id)

    # 입장 시: 기존 메시지 전부 읽음 처리 + 온라인 멤버에게 read_update 브로드캐스트
    if safe_user_id != "guest":
        try:
            user_uuid = uuid.UUID(safe_user_id)
            async with AsyncSessionLocal() as db:
                chat_ids_result = await db.execute(
                    select(PartyChat.id).where(
                        PartyChat.party_id == uuid.UUID(party_id),
                        PartyChat.is_deleted == False,
                    )
                )
                all_chat_ids = chat_ids_result.scalars().all()
                for cid in all_chat_ids:
                    stmt = pg_insert(ChatReadStatus).values(
                        chat_id=cid,
                        user_id=user_uuid,
                    ).on_conflict_do_nothing(constraint="chat_read_status_chat_id_user_id_key")
                    await db.execute(stmt)
                await db.commit()

            await manager.broadcast(party_id, {
                "type": "read_update",
                "user_id": safe_user_id,
            })
        except Exception as e:
            print(f"[READ ON CONNECT ERROR] {e}")

    try:
        while True:
            data = await ws.receive_text()

            is_blocked = await redis_client.get(blocked_key(safe_user_id))
            if is_blocked:
                await manager.send_personal(ws, {
                    "type": "error",
                    "content": "채팅이 차단되어 보낼 수 없습니다.",
                })
                continue

            now = datetime.now(timezone.utc).isoformat()
            message = {
                "type": "message",
                "party_id": party_id,
                "user_id": safe_user_id,
                "nickname": nickname,
                "content": data,
                "created_at": now,
            }

            key = redis_msg_key(party_id)
            await redis_client.rpush(key, json.dumps(message, ensure_ascii=False))
            await redis_client.ltrim(key, -200, -1)
            await redis_client.expire(key, REDIS_TTL)

            chat_id_str = None
            try:
                async with AsyncSessionLocal() as db:
                    sender_uuid = None
                    try:
                        sender_uuid = uuid.UUID(safe_user_id)
                    except (ValueError, TypeError):
                        sender_uuid = None

                    new_chat = PartyChat(
                        party_id=uuid.UUID(party_id),
                        sender_id=sender_uuid,
                        message=data,
                    )
                    db.add(new_chat)
                    await db.flush()
                    chat_id_str = str(new_chat.id)

                    # 발신자 즉시 읽음 처리
                    if sender_uuid:
                        stmt = pg_insert(ChatReadStatus).values(
                            chat_id=new_chat.id,
                            user_id=sender_uuid,
                        ).on_conflict_do_nothing(constraint="chat_read_status_chat_id_user_id_key")
                        await db.execute(stmt)

                    # 현재 채팅방에 접속 중인 다른 멤버도 즉시 읽음 처리
                    online_ids = manager.get_online_user_ids(party_id)
                    for uid_str in online_ids:
                        if uid_str == safe_user_id or uid_str == "guest":
                            continue
                        try:
                            uid = uuid.UUID(uid_str)
                            stmt = pg_insert(ChatReadStatus).values(
                                chat_id=new_chat.id,
                                user_id=uid,
                            ).on_conflict_do_nothing(constraint="chat_read_status_chat_id_user_id_key")
                            await db.execute(stmt)
                        except (ValueError, TypeError):
                            pass

                    await db.commit()
            except Exception as db_err:
                print(f"[DB ERROR] {db_err}")

            # unread_count = 전체 멤버 수 - 현재 온라인(읽은) 인원
            total_cnt = await _get_total_member_count(party_id)
            online_count = len([u for u in manager.get_online_user_ids(party_id) if u != "guest"])
            message["chat_id"] = chat_id_str
            message["unread_count"] = max(0, total_cnt - online_count)

            await manager.broadcast(party_id, message)
            asyncio.create_task(moderate_in_background(party_id, safe_user_id, data, ws))

    except WebSocketDisconnect:
        manager.disconnect(party_id, ws, safe_user_id)
    except Exception as e:
        print(f"[WS_FATAL_ERROR] {e}")
        manager.disconnect(party_id, ws, safe_user_id)
