"""In-memory registry of call sessions with expiry and capacity limits."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import generate_session_token, tokens_match
from app.schemas.call import CallStatus
from app.schemas.message import SessionEndReason

logger = get_logger(__name__)


class TooManyCallsError(Exception):
    """The server is at MAX_CONCURRENT_CALLS."""


@dataclass(slots=True)
class CallSession:
    call_id: str
    token: str
    created_at: datetime
    setup_deadline: float
    status: CallStatus = "created"
    end_reason: SessionEndReason | None = None
    ended_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def setup_expires_at(self) -> datetime:
        return self.created_at + timedelta(seconds=max(0.0, self.setup_deadline - time.monotonic()))


class CallSessionManager:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._clock = clock
        self._sessions: dict[str, CallSession] = {}
        self._janitor: asyncio.Task[None] | None = None

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def create(self) -> CallSession:
        self.purge_expired()
        if len(self._sessions) >= self._settings.max_concurrent_calls:
            raise TooManyCallsError("Too many concurrent calls.")
        session = CallSession(
            call_id=uuid.uuid4().hex,
            token=generate_session_token(),
            created_at=datetime.now(timezone.utc),
            setup_deadline=self._clock() + self._settings.call_setup_timeout_seconds,
        )
        self._sessions[session.call_id] = session
        logger.info("Call session created", extra={"call_id": session.call_id})
        return session

    def get(self, call_id: str) -> CallSession | None:
        return self._sessions.get(call_id)

    def authenticate(self, call_id: str, token: str | None) -> CallSession | None:
        session = self._sessions.get(call_id)
        if session is None or session.status == "ended":
            return None
        return session if tokens_match(session.token, token) else None

    def mark_connected(self, session: CallSession) -> None:
        session.status = "connected"

    def end(self, call_id: str, reason: SessionEndReason) -> CallSession | None:
        session = self._sessions.pop(call_id, None)
        if session is None:
            return None
        session.status = "ended"
        session.end_reason = session.end_reason or reason
        session.ended_event.set()
        logger.info("Call session ended", extra={"call_id": call_id, "reason": session.end_reason})
        return session

    def purge_expired(self) -> int:
        now = self._clock()
        expired = [
            s.call_id for s in self._sessions.values() if s.status == "created" and now > s.setup_deadline
        ]
        for call_id in expired:
            self.end(call_id, "setup_timeout")
        return len(expired)

    def start_janitor(self, interval_seconds: float = 15.0) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(interval_seconds)
                self.purge_expired()

        if self._janitor is None:
            self._janitor = asyncio.create_task(loop(), name="call-session-janitor")

    async def shutdown(self) -> None:
        if self._janitor:
            self._janitor.cancel()
            try:
                await self._janitor
            except asyncio.CancelledError:
                pass
            self._janitor = None
        for call_id in list(self._sessions):
            self.end(call_id, "server_ended")
