import json
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active: dict[str, list[WebSocket]] = {}
        self.user_ws: dict[str, dict[str, WebSocket]] = {}

    async def connect(self, party_id: str, ws: WebSocket, user_id: str = "guest"):
        await ws.accept()
        self.active.setdefault(party_id, []).append(ws)
        self.user_ws.setdefault(party_id, {})[user_id] = ws

    def disconnect(self, party_id: str, ws: WebSocket, user_id: str = "guest"):
        if party_id in self.active:
            try:
                self.active[party_id].remove(ws)
            except ValueError:
                pass
        if party_id in self.user_ws:
            self.user_ws[party_id].pop(user_id, None)

    def get_online_user_ids(self, party_id: str) -> set[str]:
        return set(self.user_ws.get(party_id, {}).keys())

    async def broadcast(self, party_id: str, message: dict):
        msg_str = json.dumps(message, ensure_ascii=False)
        if party_id in self.active:
            for ws in list(self.active[party_id]):
                try:
                    await ws.send_text(msg_str)
                except Exception:
                    self.disconnect(party_id, ws)

    async def send_personal(self, ws: WebSocket, message: dict):
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False))
        except Exception:
            pass


manager = ConnectionManager()
