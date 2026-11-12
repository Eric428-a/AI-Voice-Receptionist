"""POST /api/calls and DELETE /api/calls/{call_id}"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.api.deps import get_container
from app.schemas.call import CreateCallResponse, EndCallResponse
from app.services.calls.session_manager import TooManyCallsError
from app.services.container import ServiceContainer

router = APIRouter(prefix="/calls", tags=["calls"])
CALL_ID_PATTERN = r"^[0-9a-f]{32}$"
_CALL_ID = re.compile(CALL_ID_PATTERN)


@router.post("", response_model=CreateCallResponse, status_code=status.HTTP_201_CREATED)
async def create_call(container: ServiceContainer = Depends(get_container)) -> CreateCallResponse:
    try:
        session = container.sessions.create()
    except TooManyCallsError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "All lines are busy. Try again shortly.") from exc
    return CreateCallResponse(
        call_id=session.call_id,
        session_token=session.token,
        websocket_path=f"/api/ws/calls/{session.call_id}",
        created_at=session.created_at,
        setup_expires_at=session.setup_expires_at,
        max_duration_seconds=container.settings.max_call_duration_seconds,
    )


@router.delete("/{call_id}", response_model=EndCallResponse)
async def end_call(
    call_id: str = Path(pattern=CALL_ID_PATTERN),
    container: ServiceContainer = Depends(get_container),
) -> EndCallResponse:
    session = container.sessions.end(call_id, "client_ended")
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found or already ended.")
    return EndCallResponse(call_id=call_id, status="ended", ended=True)


def is_valid_call_id(call_id: str) -> bool:
    return bool(_CALL_ID.match(call_id))
