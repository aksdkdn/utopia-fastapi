from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.config import settings
from routers import auth, parties, notifications, chat, captcha

app = FastAPI(title="Party-Up API", description="파티업 백엔드 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(parties.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(captcha.router, prefix="/api/captcha", tags=["Captcha"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}
