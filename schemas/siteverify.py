"""
SiteVerify 요청/응답 스키마
reCAPTCHA v2 siteverify API와 호환되는 형식
"""
from typing import Optional
from pydantic import BaseModel


class SiteVerifyRequest(BaseModel):
    """파트너 서버 → Party-Up: 토큰 검증 요청"""
    secret: str                           # api_keys.secret_key
    response: str                         # 클라이언트에서 받은 JWT captcha_token
    remoteip: Optional[str] = None        # 사용자 IP (선택)


class SiteVerifyResponse(BaseModel):
    """Party-Up → 파트너 서버: 검증 결과"""
    success: bool
    challenge_ts: Optional[str] = None    # ISO 8601 챌린지 발급 시각
    hostname: Optional[str] = None        # 토큰이 발급된 호스트
    score: Optional[float] = None         # 행동 점수 (0.0~1.0)
    error_codes: Optional[list[str]] = None  # 실패 사유


class SiteKeyContext(BaseModel):
    """site_key 검증 후 라우터에 전달되는 컨텍스트"""
    api_key_id: str
    client_name: str
    api_key: str       # site_key (public)
    secret_key: str    # secret (private)
    plan: str
    allowed_domains: Optional[list[str]] = None
    monthly_limit: int
    current_month_usage: int
    matched_domain: Optional[str] = None
