"""
site_key 검증 모듈 (Model B2 SDK 인증)

1. JS SDK가 /api/captcha/init 호출 시 X-Site-Key 헤더로 site_key 전달
   → verify_site_key()로 DB 조회 + 도메인 매칭 + 쿼터 체크

2. 파트너 서버가 /api/captcha/siteverify 호출 시 secret 전달
   → lookup_secret()으로 DB 조회

- api_keys.api_key    = site_key (프론트, public)
- api_keys.secret_key = secret   (서버, private)
- api_keys.allowed_domains = Origin/Referer 매칭 대상
"""
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import AsyncSessionLocal
from schemas.siteverify import SiteKeyContext


# ══════════════════════════════════════════════════════════════
# 유틸: URL → hostname 추출
# ══════════════════════════════════════════════════════════════
def _extract_host(url_or_host: str) -> str:
    """
    Origin/Referer 등에서 호스트명만 추출한다.
    예: "https://techcorp.example.com/login?x=1" → "techcorp.example.com"
    """
    try:
        parsed = urlparse(url_or_host)
        hostname = parsed.hostname
        return hostname if hostname else url_or_host.split("/")[0]
    except Exception:
        return url_or_host


def _match_domain(request_host: str, allowed_domains: list[str]) -> Optional[str]:
    """
    allowed_domains 목록에서 요청 호스트가 매칭되는지 확인한다.
      - 정확 매칭: "techcorp.com" == "techcorp.com"
      - 와일드카드: "*.techcorp.com" → "api.techcorp.com" 매칭
      - localhost → 개발 환경 허용
    allowed_domains가 None이면 모든 도메인 허용 (제한 없음)
    """
    if allowed_domains is None:
        return None  # 제한 없음 = 허용

    request_host = request_host.lower().strip()

    for allowed in allowed_domains:
        allowed_lower = allowed.lower().strip()

        # 정확 매칭
        if request_host == allowed_lower:
            return allowed

        # 와일드카드 매칭 (*.example.com)
        if allowed_lower.startswith("*."):
            suffix = allowed_lower[1:]  # ".example.com"
            if request_host.endswith(suffix):
                return allowed

        # localhost 허용
        if allowed_lower == "localhost" and request_host.startswith("localhost"):
            return allowed

    return None  # 매칭 실패


# ══════════════════════════════════════════════════════════════
# DB 조회: api_key로 레코드 찾기
# ══════════════════════════════════════════════════════════════
async def _lookup_by_api_key(api_key: str) -> Optional[dict]:
    """api_keys 테이블에서 api_key(site_key)로 레코드 조회"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
            SELECT id, client_name, api_key, secret_key, allowed_domains,
                   monthly_limit, current_month_usage, plan, is_active
            FROM api_keys
            WHERE api_key = :api_key
            LIMIT 1
            """),
            {"api_key": api_key},
        )
        row = result.mappings().first()
        return dict(row) if row else None


# ══════════════════════════════════════════════════════════════
# DB 조회: secret_key로 레코드 찾기 (siteverify용)
# ══════════════════════════════════════════════════════════════
async def lookup_secret(secret: str) -> Optional[dict]:
    """api_keys 테이블에서 secret_key로 레코드 조회 (/siteverify용)"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
            SELECT id, client_name, api_key, secret_key, allowed_domains,
                   monthly_limit, current_month_usage, plan, is_active
            FROM api_keys
            WHERE secret_key = :secret
            LIMIT 1
            """),
            {"secret": secret},
        )
        row = result.mappings().first()
        return dict(row) if row else None


# ══════════════════════════════════════════════════════════════
# 사용량 증가 + 로그 기록
# ══════════════════════════════════════════════════════════════
async def _increment_usage(api_key_id: str):
    """월간 사용량 1 증가"""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
            UPDATE api_keys
            SET current_month_usage = current_month_usage + 1
            WHERE id = :id
            """),
            {"id": api_key_id},
        )
        await db.commit()


async def log_siteverify_usage(
    api_key_id: str,
    endpoint: str,
    client_ip: str,
    origin_domain: Optional[str],
    status_code: int,
    response_time_ms: int,
):
    """api_usage_logs에 사용 기록 저장"""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("""
                INSERT INTO api_usage_logs
                    (api_key_id, endpoint, client_ip, origin_domain,
                     status_code, response_time_ms)
                VALUES
                    (:api_key_id, :endpoint, CAST(:client_ip AS INET), :origin_domain,
                     :status_code, :response_time_ms)
                """),
                {
                    "api_key_id": api_key_id,
                    "endpoint": endpoint,
                    "client_ip": client_ip or "0.0.0.0",
                    "origin_domain": origin_domain,
                    "status_code": status_code,
                    "response_time_ms": response_time_ms,
                },
            )
            await db.commit()
    except Exception:
        pass  # 로그 실패가 요청을 막으면 안 됨


# ══════════════════════════════════════════════════════════════
# 메인: site_key 검증 (SDK init/challenge/verify에서 사용)
# ══════════════════════════════════════════════════════════════
async def verify_site_key(request: Request) -> Optional[SiteKeyContext]:
    """
    SDK 요청의 X-Site-Key 헤더를 검증한다.
    - 없으면 None 반환 (기존 내부 캡챠는 site_key 없이 동작)
    - 있으면 api_keys DB 조회 → 활성화/도메인/쿼터 체크
    - 실패 시 HTTPException

    Note: site_key가 None이면 JWT 기반 내부 인증으로 fallback
    """
    site_key = request.headers.get("X-Site-Key") or request.query_params.get("site_key")

    if not site_key:
        return None  # 내부 호출 (site_key 없음)

    # DB 조회
    row = await _lookup_by_api_key(site_key)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_codes": ["invalid-site-key"]},
        )

    if not row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_codes": ["site-key-disabled"]},
        )

    # 도메인 매칭
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    origin_host = _extract_host(origin) if origin else None
    referer_host = _extract_host(referer) if referer else None
    request_host = origin_host or referer_host

    matched_domain = None
    if row["allowed_domains"] and request_host:
        matched_domain = _match_domain(request_host, row["allowed_domains"])
        if matched_domain is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error_codes": ["hostname-mismatch"]},
            )

    # 쿼터 체크
    if row["current_month_usage"] >= row["monthly_limit"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error_codes": ["quota-exceeded"]},
        )

    # 사용량 증가
    await _increment_usage(str(row["id"]))

    # 사용 로그
    client_ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP")
        or (request.client.host if request.client else "0.0.0.0")
    )

    await log_siteverify_usage(
        api_key_id=str(row["id"]),
        endpoint=request.url.path,
        client_ip=client_ip,
        origin_domain=request_host,
        status_code=200,
        response_time_ms=0,
    )

    return SiteKeyContext(
        api_key_id=str(row["id"]),
        client_name=row["client_name"],
        api_key=row["api_key"],
        secret_key=row["secret_key"],
        plan=row["plan"],
        allowed_domains=row["allowed_domains"],
        monthly_limit=row["monthly_limit"],
        current_month_usage=row["current_month_usage"],
        matched_domain=matched_domain,
    )
