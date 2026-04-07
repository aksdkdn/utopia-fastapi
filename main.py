import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.database import Base, engine
from routers import auth, captcha, chat, notifications, parties


# ✅ Fix: @app.on_event("startup") deprecated → lifespan으로 교체
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버 실행 시 테이블 없을 경우 DB 자동생성
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="Party-Up API",
    description="파티업 백엔드 API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 응답 시간 측정 미들웨어 (병목 진단용)
# 200ms 이상 걸리는 요청만 로그로 출력 → 콘솔 노이즈 최소화
@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms >= 200:
        print(f"[TIMING] {elapsed_ms:7.0f}ms  {request.method} {request.url.path}")
    return response

app.include_router(auth.router, prefix="/api")
app.include_router(parties.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(captcha.router, prefix="/api/captcha", tags=["Captcha"])

#도상원
ANIMAL_ASSET_DIR = Path(__file__).resolve().parent.parent / "animal"
if ANIMAL_ASSET_DIR.exists():
    app.mount(
        "/animal-assets",
        StaticFiles(directory=str(ANIMAL_ASSET_DIR)),
        name="animal-assets",
    )
#도상원


# 헬스체크
@app.get("/api/health")
async def health():
    return {"status": "ok"}
