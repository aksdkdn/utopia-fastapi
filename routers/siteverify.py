"""
/api/captcha/siteverify — 파트너 서버가 캡챠 토큰을 검증하는 S2S API

reCAPTCHA v2 siteverify API와 호환되는 형식으로,
파트너 서버가 클라이언트로부터 받은 JWT captcha_token을 검증한다.

흐름:
  1. 사용자가 캡챠 통과 → JS SDK가 JWT captcha_token 발급
     (behavior_captcha의 _issue_captcha_token; site_key 바인딩)
  2. 사용자가 captcha_token을 파트너 서버로 전달 (폼 submit 등)
  3. 파트너 서버가 POST /api/captcha/siteverify (secret + token)으로 검증
  4. Party-Up이 JWT 디코딩 + Redis(captcha:token:{jti}) 일회성 체크 + site_key 매칭
  5. 검증 결과 반환 (reCAPTCHA 호환: success, challenge_ts, hostname, score, error_codes)

      HandOCR(captcha.py) 토큰도 captcha_pass:{token} 형태로 별도 검증 가능

cURL 예시:
  curl -X POST https://api.party-up.xyz/api/captcha/siteverify \\
       -H "Content-Type: application/json" \\
       -d '{"secret":"...","response":"<JWT>","remoteip":"1.2.3.4"}'
"""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from jose import JWTError, jwt

from core.config import settings
from core.redis_client import redis_client
from core.site_key_auth import lookup_secret, log_siteverify_usage
from schemas.siteverify import SiteVerifyRequest, SiteVerifyResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/captcha", tags=["captcha-sdk"])

# 캡챠 JWT 시크릿: 별도 설정이 없으면 메인 SECRET_KEY 사용
_CAPTCHA_SECRET = getattr(settings, "CAPTCHA_JWT_SECRET", "") or settings.SECRET_KEY


def _token_key(jti: str) -> str:
    """Redis에 저장된 캡챠 토큰 키"""
    return f"captcha:token:{jti}"


@router.post(
    "/siteverify",
    response_model=SiteVerifyResponse,
    summary="캡챠 토큰 검증 (reCAPTCHA v2 호환)",
)
async def siteverify(payload: SiteVerifyRequest, request: Request):
    """
    파트너 서버가 JWT 캡챠 토큰을 검증한다.
    반환: success=true, challenge_ts, hostname, score
    실패: success=false, error_codes
    """
    started_at = time.monotonic()
    client_ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP")
        or (request.client.host if request.client else "0.0.0.0")
    )

    # ── 1. secret 검증 ──
    row = await lookup_secret(payload.secret)
    if not row:
        return SiteVerifyResponse(
            success=False,
            error_codes=["invalid-input-secret"],
        )

    if not row["is_active"]:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        await log_siteverify_usage(
            api_key_id=str(row["id"]),
            endpoint="/siteverify",
            client_ip=client_ip,
            origin_domain=None,
            status_code=403,
            response_time_ms=elapsed_ms,
        )
        return SiteVerifyResponse(
            success=False,
            error_codes=["site-key-disabled"],
        )

    expected_site_key = row["api_key"]
    api_key_id = str(row["id"])

    # ── 2. JWT 디코딩 ──
    try:
        jwt_payload = jwt.decode(
            payload.response,
            _CAPTCHA_SECRET,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        await log_siteverify_usage(
            api_key_id=api_key_id,
            endpoint="/siteverify",
            client_ip=client_ip,
            origin_domain=None,
            status_code=400,
            response_time_ms=elapsed_ms,
        )
        return SiteVerifyResponse(
            success=False,
            error_codes=["invalid-input-response"],
        )

    # ── 3. 토큰 타입 확인 ──
    token_type = jwt_payload.get("type")
    if token_type not in ("captcha", getattr(settings, "CAPTCHA_JWT_TYPE", "captcha")):
        return SiteVerifyResponse(
            success=False,
            error_codes=["invalid-input-response"],
        )

    # ── 4. Redis 일회성 체크 ──
    jti = jwt_payload.get("jti")
    if jti:
        token_state_raw = await redis_client.get(_token_key(jti))
        if token_state_raw is None:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            await log_siteverify_usage(
                api_key_id=api_key_id,
                endpoint="/siteverify",
                client_ip=client_ip,
                origin_domain=None,
                status_code=410,
                response_time_ms=elapsed_ms,
            )
            return SiteVerifyResponse(
                success=False,
                error_codes=["timeout-or-duplicate"],
            )

        # Redis 토큰 상태 파싱
        try:
            token_state = json.loads(token_state_raw)
        except (json.JSONDecodeError, TypeError):
            token_state = {}

        # site_key 바인딩 체크
        issued_site_key = token_state.get("site_key")
        if issued_site_key and issued_site_key != expected_site_key:
            return SiteVerifyResponse(
                success=False,
                error_codes=["invalid-input-response"],
            )

        # 토큰 소비 (일회성)
        await redis_client.delete(_token_key(jti))

    # ── 5. 결과 조립 ──
    score = jwt_payload.get("score")
    if isinstance(score, str):
        try:
            score = float(score)
        except ValueError:
            score = None

    iat = jwt_payload.get("iat")
    challenge_ts = None
    if iat:
        try:
            challenge_ts = datetime.fromtimestamp(iat, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            pass

    hostname = jwt_payload.get("hostname")

    # 사용량 로그
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    await log_siteverify_usage(
        api_key_id=api_key_id,
        endpoint="/siteverify",
        client_ip=client_ip,
        origin_domain=hostname,
        status_code=200,
        response_time_ms=elapsed_ms,
    )

    return SiteVerifyResponse(
        success=True,
        challenge_ts=challenge_ts,
        hostname=hostname,
        score=score,
    )
