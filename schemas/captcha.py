from typing import Literal

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# 1차 캡챠 요청 모델
# ─────────────────────────────────────────────


class CaptchaMouseMove(BaseModel):
    # 프론트에서 수집한 마우스 좌표와 시각(ms) 정보
    x: float
    y: float
    t: int = Field(ge=0)


class CaptchaClickEvent(BaseModel):
    # 클릭 좌표와 타깃 태그명
    x: float
    y: float
    t: int = Field(ge=0)
    target: str


class CaptchaScreenInfo(BaseModel):
    # 사용자의 브라우저 화면 크기
    width: int = Field(ge=0)
    height: int = Field(ge=0)


class CaptchaEnvInfo(BaseModel):
    # 브라우저 환경 일관성 점검에 사용하는 최소 환경 정보
    webdriver: bool = False
    plugins_count: int = Field(default=0, ge=0)
    canvas_hash: str = ""
    webgl_renderer: str = ""
    screen: CaptchaScreenInfo
    timezone: str = ""
    languages: list[str] = Field(default_factory=list)


class CaptchaInitRequest(BaseModel):
    # 행동 분석에 필요한 사용자 이벤트 묶음
    mouse_moves: list[CaptchaMouseMove] = Field(default_factory=list)
    clicks: list[CaptchaClickEvent] = Field(default_factory=list)
    key_intervals: list[int] = Field(default_factory=list)
    scrolled: bool = False
    env: CaptchaEnvInfo
    page_load_to_checkbox: int = Field(ge=0)
    session_id: str = ""
    timestamp: str = ""
    trigger_type: Literal["register", "new_ip_login", "login_fail"] = "new_ip_login"


# ─────────────────────────────────────────────
# 1차 캡챠 응답 모델
# ─────────────────────────────────────────────


class CaptchaInitResponse(BaseModel):
    # pass / challenge / block 세 가지 분기만 프론트에 노출
    status: Literal["pass", "challenge", "block"]
    token: str | None = None
    session_id: str | None = None
    message: str | None = None


class CaptchaEmojiItem(BaseModel):
    id: str
    url: str
    category: str


class CaptchaPhotoItem(BaseModel):
    id: str
    url: str
    index: int = Field(ge=0, le=8)


class CaptchaChallengeResponse(BaseModel):
    session_id: str
    emojis: list[CaptchaEmojiItem]
    photos: list[CaptchaPhotoItem]


class CaptchaVerifyRequest(BaseModel):
    session_id: str
    selected_indices: list[int] = Field(default_factory=list)


class CaptchaVerifyResponse(BaseModel):
    success: bool
    token: str | None = None
    remaining_attempts: int | None = None
    message: str | None = None


class CaptchaStatusResponse(BaseModel):
    # 운영/디버깅용 상태 응답
    status: Literal["NORMAL", "WAIT", "LOCKED", "BANNED"]
    message: str
    retry_after_seconds: int | None = None
    # 이번 수정: 사용자가 모달을 닫거나 새로고침해도 같은 challenge를 이어서 풀게 만들기 위한 세션 ID
    # 사용자가 모달을 닫거나 새로고침해도 이어서 풀어야 하는 challenge 세션 ID
    active_session_id: str | None = None
