"""HTTP schemas for call session management."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

CallStatus = Literal["created", "connected", "ended"]


class CreateCallResponse(BaseModel):
    call_id: str
    session_token: str
    websocket_path: str
    created_at: datetime
    setup_expires_at: datetime
    max_duration_seconds: int


class CallInfo(BaseModel):
    call_id: str
    status: CallStatus
    created_at: datetime


class EndCallResponse(BaseModel):
    call_id: str
    status: CallStatus
    ended: bool
