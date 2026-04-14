import asyncio
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError

from core.database import AsyncSessionLocal
from services.auth_service import decode_access_token
from services.notification_service import get_unread_notification_count_service
from services.notification_ws_service import notification_connection_manager


router = APIRouter(tags=["notifications-ws"])


async def get_user_id_from_websocket(websocket: WebSocket) -> uuid.UUID:
    access_token = websocket.cookies.get("access_token")

    if not access_token:
        await websocket.close(code=4401)
        raise WebSocketDisconnect

    try:
        payload = decode_access_token(access_token)
        user_id_str = payload.get("sub")

        if not user_id_str:
            await websocket.close(code=4401)
            raise WebSocketDisconnect

        return uuid.UUID(user_id_str)

    except (JWTError, ValueError, TypeError):
        await websocket.close(code=4401)
        raise WebSocketDisconnect
    except Exception:
        await websocket.close(code=4401)
        raise WebSocketDisconnect


@router.websocket("/ws/notifications")
async def notifications_websocket(websocket: WebSocket):
    user_id = await get_user_id_from_websocket(websocket)
    await notification_connection_manager.connect(user_id, websocket)

    try:
        async with AsyncSessionLocal() as db:
            unread_count = await get_unread_notification_count_service(
                db=db,
                user_id=user_id,
            )

        await websocket.send_json(
            {
                "type": "connected",
                "message": "알림 웹소켓 연결 성공",
                "unread_count": unread_count,
            }
        )

        while True:
            try:
                raw_message = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30,
                )

                if raw_message == "ping":
                    await websocket.send_json({"type": "pong"})
                else:
                    await websocket.send_json({"type": "pong"})

            except asyncio.TimeoutError:
                # 일정 시간 동안 클라이언트 메시지가 없어도 연결 유지용 응답 전송
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        notification_connection_manager.disconnect(user_id, websocket)
    except Exception:
        notification_connection_manager.disconnect(user_id, websocket)
        try:
            await websocket.close()
        except Exception:
            pass