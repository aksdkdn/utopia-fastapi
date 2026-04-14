import json
from collections import defaultdict
from uuid import UUID

from fastapi import WebSocket

# 유저별로 웹소켓 연결 관리
class NotificationConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[str(user_id)].add(websocket)

    def disconnect(self, user_id: UUID, websocket: WebSocket) -> None:
        user_key = str(user_id)
        if user_key not in self._connections:
            return

        self._connections[user_key].discard(websocket)

        if not self._connections[user_key]:
            del self._connections[user_key]

    async def send_to_user(self, user_id: UUID, payload: dict) -> None:
        user_key = str(user_id)
        connections = list(self._connections.get(user_key, set()))

        stale_connections: list[WebSocket] = []

        for websocket in connections:
            try:
                await websocket.send_text(json.dumps(payload, default=str))
            except Exception:
                stale_connections.append(websocket)

        for websocket in stale_connections:
            self.disconnect(user_id, websocket)

    # 해당 유저 접속 확인
    def has_connections(self, user_id: UUID) -> bool:
        return bool(self._connections.get(str(user_id)))


notification_connection_manager = NotificationConnectionManager()