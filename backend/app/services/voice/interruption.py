"""Tracks the assistant's active response and implements barge-in cancellation.

"Audible" is tracked from the audio actually sent: the browser reports
``playback.finished``; as a safety net, playback is also considered finished
once the sent audio duration (plus a grace period) has elapsed.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

PLAYBACK_GRACE_SECONDS = 1.5


@dataclass(slots=True)
class ResponseHandle:
    response_id: int
    task: asyncio.Task[None] | None = None
    audio_seconds: float = 0.0
    first_audio_at: float | None = None
    stream_complete: bool = False
    playback_finished: bool = False
    spoken_segments: list[str] = field(default_factory=list)


class InterruptionController:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._current: ResponseHandle | None = None
        self._next_id = 1

    @property
    def current(self) -> ResponseHandle | None:
        return self._current

    def new_response(self) -> ResponseHandle:
        handle = ResponseHandle(response_id=self._next_id)
        self._next_id += 1
        self._current = handle
        return handle

    def note_audio(self, handle: ResponseHandle, seconds: float) -> None:
        if handle.first_audio_at is None:
            handle.first_audio_at = self._clock()
        handle.audio_seconds += seconds

    def mark_playback_finished(self, response_id: int) -> bool:
        handle = self._current
        if handle is None or handle.response_id != response_id:
            return False
        handle.playback_finished = True
        return True

    def is_generating(self) -> bool:
        handle = self._current
        return bool(handle and handle.task and not handle.task.done())

    def is_audible(self) -> bool:
        """True while assistant audio may be coming out of the caller's speaker."""
        handle = self._current
        if handle is None or handle.first_audio_at is None or handle.playback_finished:
            return False
        if handle.task is not None and not handle.task.done():
            return True
        expected_end = handle.first_audio_at + handle.audio_seconds + PLAYBACK_GRACE_SECONDS
        return self._clock() < expected_end

    def is_active(self) -> bool:
        return self.is_generating() or self.is_audible()

    async def cancel_active(self) -> ResponseHandle | None:
        """Cancel the current response (generation and/or playback). Returns it if it was active."""
        handle = self._current
        if handle is None or not self.is_active():
            return None
        if handle.task is not None and not handle.task.done():
            handle.task.cancel()
            try:
                await handle.task
            except asyncio.CancelledError:
                pass
            except Exception:  # task errors are reported by the task itself
                pass
        handle.playback_finished = True
        return handle

    async def shutdown(self) -> None:
        handle = self._current
        if handle and handle.task and not handle.task.done():
            handle.task.cancel()
            try:
                await handle.task
            except (asyncio.CancelledError, Exception):
                pass
