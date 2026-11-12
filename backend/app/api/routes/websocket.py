"""WebSocket /api/ws/calls/{call_id}

Handshake: Origin is validated, then the first message must be
``{"type": "session.auth", "token": ...}`` (the token is not put in the URL so
it never appears in access logs).
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.api.routes.calls import is_valid_call_id
from app.core.logging import get_logger
from app.core.security import is_origin_allowed
from app.services.calls.call_manager import CallOrchestrator
from app.services.container import ServiceContainer

router = APIRouter(tags=["websocket"])
logger = get_logger(__name__)

POLICY_VIOLATION = 1008


class StarletteCallTransport:
    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket

    async def receive(self) -> str | bytes | None:
        try:
            message = await self._ws.receive()
        except (WebSocketDisconnect, RuntimeError):
            return None
        if message.get("type") == "websocket.disconnect":
            return None
        if message.get("bytes") is not None:
            return message["bytes"]
        if message.get("text") is not None:
            return message["text"]
        return b""

    async def send_text(self, data: str) -> None:
        await self._ws.send_text(data)

    async def close(self, code: int = 1000) -> None:
        if self._ws.application_state == WebSocketState.CONNECTED:
            await self._ws.close(code=code)


@router.websocket("/ws/calls/{call_id}")
async def call_socket(websocket: WebSocket, call_id: str) -> None:
    container: ServiceContainer = websocket.app.state.container
    if not is_origin_allowed(websocket.headers.get("origin"), container.settings):
        logger.warning("Rejected WebSocket origin", extra={"call_id": call_id})
        await websocket.close(code=POLICY_VIOLATION)
        return
    session = container.sessions.get(call_id) if is_valid_call_id(call_id) else None
    if session is None or session.status != "created":
        await websocket.close(code=POLICY_VIOLATION)
        return
    await websocket.accept()
    orchestrator = CallOrchestrator(session, StarletteCallTransport(websocket), container.call_services())
    await orchestrator.run()
