import time
import logging
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from sqlalchemy import text

from core.config import settings
from core.database import AsyncSessionLocal, Base, engine
from models.admin import ActivityLog

from routers import admin, assets, auth, behavior_captcha, captcha, chat, notifications, parties, report, ws_notifications, payments, admin_handocr, praises, search, siteverify

from routers.mypage import profile, trust_history
from routers.user import referrers
from routers.mypage import parties as mypage_parties
from routers.mypage import payments as mypage_payments

from routers.quick_match import router as quick_match_router

from routers.admin_moderation_config import router as admin_mod_config_router
from routers.admin.cloud_monitor import router as admin_cloud_monitor_router
from routers.admin import admin_quick_match
from routers.appeal import router as appeal_router
from routers.appeal import router as appeal_router

logging.basicConfig(level=logging.DEBUG)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'services'
                          AND column_name = 'original_price'
                    ) THEN
                        EXECUTE 'ALTER TABLE services ADD COLUMN original_price INTEGER';
                    END IF;

                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'services'
                          AND column_name = 'selling_price'
                    ) THEN
                        EXECUTE 'UPDATE services SET monthly_price = COALESCE(selling_price, monthly_price)';
                        EXECUTE 'ALTER TABLE services DROP COLUMN selling_price';
                    END IF;

                    EXECUTE 'UPDATE services SET original_price = COALESCE(original_price, monthly_price) WHERE original_price IS NULL';

                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'admin_roles'
                          AND column_name = 'can_manage_payments'
                    ) THEN
                        EXECUTE 'ALTER TABLE admin_roles ADD COLUMN can_manage_payments BOOLEAN NOT NULL DEFAULT false';
                    END IF;

                    EXECUTE '
                        UPDATE admin_roles
                        SET can_manage_payments = true
                        WHERE can_manage_admins = true
                           OR can_approve_settlements = true
                    ';

                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'admin_roles'
                          AND column_name = 'can_approve_receipts'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'admin_roles'
                          AND column_name = 'can_manage_captcha'
                    ) THEN
                        EXECUTE '
                            ALTER TABLE admin_roles
                            RENAME COLUMN can_approve_receipts TO can_manage_captcha
                        ';
                    END IF;

                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'admin_roles'
                          AND column_name = 'can_manage_captcha'
                    ) THEN
                        EXECUTE '
                            ALTER TABLE admin_roles
                            ADD COLUMN can_manage_captcha BOOLEAN NOT NULL DEFAULT false
                        ';
                    END IF;

                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'admin_roles'
                          AND column_name = 'can_manage_handocr'
                    ) THEN
                        EXECUTE 'ALTER TABLE admin_roles ADD COLUMN can_manage_handocr BOOLEAN NOT NULL DEFAULT false';
                    END IF;

                    EXECUTE '
                        UPDATE admin_roles
                        SET can_manage_handocr = true
                        WHERE can_manage_admins = true
                           OR can_manage_captcha = true
                    ';

                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'activity_logs'
                          AND column_name = 'ip_address'
                          AND udt_name <> 'inet'
                    ) THEN
                        EXECUTE '
                            ALTER TABLE activity_logs
                            ALTER COLUMN ip_address TYPE inet
                            USING CASE
                                WHEN ip_address IS NULL OR btrim(ip_address::text) = '''' THEN NULL
                                ELSE ip_address::inet
                            END
                        ';
                    END IF;
                END
                $$;
                """
            )
        )
    yield

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

app = FastAPI(
    title="Party-Up API",
    description="파티업 백엔드 API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

def _extract_actor_user_id(request: Request) -> uuid.UUID | None:
    access_token = request.cookies.get("access_token")
    if not access_token:
        return None

    try:
        payload = jwt.decode(access_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != "access":
            return None
        return uuid.UUID(payload.get("sub", ""))
    except (JWTError, ValueError):
        return None

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_exceptions(request: Request, call_next):
    if request.headers.get("upgrade") == "websocket":
        return await call_next(request)
        
    try:
        return await call_next(request)
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        headers = {}
        origin = request.headers.get("origin")
        if origin and origin in settings.ALLOWED_ORIGINS:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Credentials"] = "true"
            headers["Vary"] = "Origin"
        return JSONResponse(status_code=500, content={"detail": str(e)}, headers=headers)

@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    if request.headers.get("upgrade") == "websocket":
        return await call_next(request)

    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms >= 200:
        print(f"[TIMING] {elapsed_ms:7.0f}ms  {request.method} {request.url.path}")
    return response



@app.middleware("http")
async def admin_access_log_middleware(request: Request, call_next):
    if request.headers.get("upgrade") == "websocket":
        return await call_next(request)

    response = await call_next(request)

    if request.url.path.startswith("/api/admin") and request.url.path != "/api/admin/logs":
        try:
            async with AsyncSessionLocal() as session:
                session.add(
                    ActivityLog(
                        actor_user_id=_extract_actor_user_id(request),
                        action_type="admin_access",
                        description=f"{request.method} {request.url.path} -> {response.status_code}",
                        ip_address=request.client.host if request.client else None,
                        extra_metadata={"path": request.url.path},
                    )
                )
                await session.commit()
        except Exception:
            logging.exception("관리자 접근 로그 기록 실패")

    return response

app.include_router(auth.router, prefix="/api")
app.include_router(parties.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(quick_match_router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(ws_notifications.router)
app.include_router(chat.router, prefix="/api")
app.include_router(behavior_captcha.router, prefix="/api")  
app.include_router(captcha.router, prefix="/api")
app.include_router(siteverify.router) 
app.include_router(assets.router, prefix="/api", tags=["Assets"])

app.include_router(admin.router, prefix="/api")  
app.include_router(report.router, prefix="/api")  


app.include_router(profile.router, prefix="/api")
app.include_router(mypage_parties.router, prefix="/api")
app.include_router(trust_history.router, prefix="/api")
app.include_router(mypage_payments.router, prefix="/api")
app.include_router(referrers.router, prefix="/api")

app.include_router(admin_mod_config_router, prefix="/api")
app.include_router(admin_cloud_monitor_router, prefix="/api")
app.include_router(admin_quick_match.router, prefix="/api")
app.include_router(appeal_router)

app.include_router(appeal_router)

app.include_router(admin_handocr.router, prefix="/api")


app.include_router(praises.router, prefix='/api')


app.include_router(search.router, prefix='/api')


_sdk_dir = Path(__file__).resolve().parent / "sdk"
if _sdk_dir.is_dir():
    app.mount("/sdk", StaticFiles(directory=str(_sdk_dir)), name="sdk")

@app.get("/api/health")
async def health():
    return {"status": "ok"}
