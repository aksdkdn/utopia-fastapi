import json
from collections import defaultdict
from uuid import UUID

from fastapi import WebSocket

# 유저별로 웹소켓 연결 관리
class NotificationConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._ip_to_users: dict[str, set[str]] = defaultdict(set)  # IP → user_id 집합
        self._user_to_ip: dict[str, str] = {}  # user_id → IP

    async def connect(self, user_id: UUID, websocket: WebSocket, ip: str | None = None) -> None:
        await websocket.accept()
        user_key = str(user_id)
        self._connections[user_key].add(websocket)
        if ip:
            self._ip_to_users[ip].add(user_key)
            self._user_to_ip[user_key] = ip

    def disconnect(self, user_id: UUID, websocket: WebSocket) -> None:
        user_key = str(user_id)
        if user_key not in self._connections:
            return

        self._connections[user_key].discard(websocket)

        if not self._connections[user_key]:
            del self._connections[user_key]
            # IP 역방향 매핑도 정리
            ip = self._user_to_ip.pop(user_key, None)
            if ip:
                self._ip_to_users[ip].discard(user_key)
                if not self._ip_to_users[ip]:
                    del self._ip_to_users[ip]

    def get_users_by_ip(self, ip: str) -> set[str]:
        """해당 IP로 현재 접속 중인 user_id 집합 반환"""
        return set(self._ip_to_users.get(ip, set()))

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
