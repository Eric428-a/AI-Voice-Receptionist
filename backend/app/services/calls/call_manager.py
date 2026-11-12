"""Real-time call orchestration for one WebSocket connection (text only).

The browser recognizes the caller's speech and speaks the replies. Per turn::

    browser transcript (user.text) -> retrieval -> OpenRouter stream (text)
        -> sentence segmenter -> response.sentence -> browser speech synthesis

Barge-in: when the browser reports the caller started talking
(``user.speaking``) while a reply is generating or being spoken, the reply
task is cancelled (aborting the OpenRouter stream), the browser is told to
stop speaking, and the part already sent for speaking stays in the
conversation marked as interrupted.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, get_args

from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import HaumexError
from app.core.logging import get_logger
from app.schemas.message import (
    Capabilities,
    ErrorCode,
    ErrorMessage,
    PingMessage,
    PlaybackFinishedMessage,
    PongMessage,
    ResponseInterruptedMessage,
    ResponseKind,
    ResponseSentenceMessage,
    ResponseStartMessage,
    ResponseTextDeltaMessage,
    ResponseTextDoneMessage,
    SessionAuthMessage,
    SessionEndedMessage,
    SessionEndMessage,
    SessionEndReason,
    SessionReadyMessage,
    SessionStartMessage,
    SpeechSettings,
    StateMessage,
    UserSpeakingMessage,
    UserTextMessage,
    parse_client_message,
)
from app.services.calls.conversation import Conversation
from app.services.calls.session_manager import CallSession, CallSessionManager
from app.services.openrouter.chat import ChatService
from app.services.openrouter.prompts import PromptLibrary
from app.services.rag.context_builder import ContextBuilder
from app.services.rag.retriever import Retriever, build_retrieval_query
from app.services.voice.interruption import InterruptionController, ResponseHandle
from app.services.voice.text_to_speech import SpeechSegmenter, estimate_speech_seconds, prepare_text_for_speech

logger = get_logger(__name__)

AUTH_TIMEOUT_SECONDS = 10.0
_VALID_ERROR_CODES: frozenset[str] = frozenset(get_args(ErrorCode))


class ConnectionClosedError(Exception):
    """The WebSocket is gone; stop sending."""


class CallTransport(Protocol):
    async def receive(self) -> str | bytes | None:
        """Next text or binary frame, or ``None`` when the client disconnected."""

    async def send_text(self, data: str) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


@dataclass(slots=True)
class CallServices:
    settings: Settings
    sessions: CallSessionManager
    chat: ChatService
    prompts: PromptLibrary
    retriever: Retriever
    context_builder: ContextBuilder
    knowledge_documents: int = 0


def _error_code(exc: BaseException, default: ErrorCode) -> ErrorCode:
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and code in _VALID_ERROR_CODES else default  # type: ignore[return-value]


async def _single(text: str) -> AsyncIterator[str]:
    yield text


class CallOrchestrator:
    def __init__(self, session: CallSession, transport: CallTransport, services: CallServices) -> None:
        self._session = session
        self._transport = transport
        self._services = services
        self._settings = services.settings
        self._conversation = Conversation()
        self._controller = InterruptionController()
        self._send_lock = asyncio.Lock()
        self._started = False
        self._closed = False
        self._background: set[asyncio.Task[None]] = set()
        self._end_reason: SessionEndReason = "client_ended"

    # ------------------------------------------------------------------ sending
    async def _send(self, message: BaseModel) -> None:
        if self._closed:
            raise ConnectionClosedError()
        try:
            async with self._send_lock:
                await self._transport.send_text(message.model_dump_json())
        except Exception as exc:
            self._closed = True
            raise ConnectionClosedError() from exc

    async def _send_error(self, code: ErrorCode, message: str, recoverable: bool = True) -> None:
        with contextlib.suppress(ConnectionClosedError):
            await self._send(ErrorMessage(code=code, message=message, recoverable=recoverable))

    async def _send_state(self, state: str, response_id: int | None = None) -> None:
        await self._send(StateMessage(state=state, response_id=response_id))  # type: ignore[arg-type]

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        receive_task: asyncio.Task[str | bytes | None] | None = None
        end_task = asyncio.create_task(self._session.ended_event.wait())
        watchdog = asyncio.create_task(self._max_duration_watchdog())
        try:
            if not await self._authenticate():
                self._end_reason = "unauthorized"
                return
            self._services.sessions.mark_connected(self._session)
            await self._send(
                SessionReadyMessage(
                    call_id=self._session.call_id,
                    capabilities=Capabilities(
                        llm=self._services.chat.configured,
                        knowledge_documents=self._services.knowledge_documents,
                    ),
                    speech=SpeechSettings(
                        language=self._settings.speech_language, rate=self._settings.speech_rate
                    ),
                    max_duration_seconds=self._settings.max_call_duration_seconds,
                )
            )
            while True:
                receive_task = asyncio.create_task(self._transport.receive())
                done, _ = await asyncio.wait({receive_task, end_task}, return_when=asyncio.FIRST_COMPLETED)
                if end_task in done:
                    self._end_reason = self._session.end_reason or "server_ended"
                    with contextlib.suppress(ConnectionClosedError):
                        await self._send(SessionEndedMessage(reason=self._end_reason))
                    break
                item = receive_task.result()
                receive_task = None
                if item is None:
                    self._end_reason = "client_ended"
                    break
                if isinstance(item, bytes):
                    continue  # audio never comes to the server in this design
                if not await self._on_text(item):
                    break
        except ConnectionClosedError:
            self._end_reason = "client_ended"
        except Exception:
            logger.exception("Call orchestrator failed", extra={"call_id": self._session.call_id})
            self._end_reason = "error"
            await self._send_error("internal_error", "The call hit an unexpected error.", recoverable=False)
        finally:
            for task in (receive_task, end_task, watchdog):
                if task is not None and not task.done():
                    task.cancel()
            await self._cleanup()

    async def _authenticate(self) -> bool:
        try:
            raw = await asyncio.wait_for(self._transport.receive(), timeout=AUTH_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raw = None
        message = None
        if isinstance(raw, str):
            with contextlib.suppress(ValidationError):
                message = parse_client_message(raw)
        if (
            isinstance(message, SessionAuthMessage)
            and self._services.sessions.authenticate(self._session.call_id, message.token) is not None
        ):
            return True
        await self._send_error("unauthorized", "Call authentication failed.", recoverable=False)
        return False

    async def _max_duration_watchdog(self) -> None:
        await asyncio.sleep(self._settings.max_call_duration_seconds)
        self._services.sessions.end(self._session.call_id, "max_duration")

    async def _cleanup(self) -> None:
        await self._controller.shutdown()
        for task in list(self._background):
            task.cancel()
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
        self._services.sessions.end(self._session.call_id, self._end_reason)
        if not self._closed:
            self._closed = True
            with contextlib.suppress(Exception):
                await self._transport.close(1000)
        logger.info(
            "Call closed",
            extra={"call_id": self._session.call_id, "reason": self._end_reason,
                   "turns": len(self._conversation.turns)},
        )

    # ------------------------------------------------------------------ inbound
    async def _on_text(self, raw: str) -> bool:
        try:
            message = parse_client_message(raw)
        except ValidationError:
            await self._send_error("invalid_message", "Message did not match the call protocol.")
            return True
        if isinstance(message, SessionStartMessage):
            self._start_conversation()
        elif isinstance(message, UserSpeakingMessage):
            if self._started:
                await self._interrupt()
        elif isinstance(message, UserTextMessage):
            if self._started:
                await self._interrupt()
                self._start_user_turn(message.text.strip())
        elif isinstance(message, PlaybackFinishedMessage):
            if self._controller.mark_playback_finished(message.response_id) and not self._controller.is_generating():
                await self._send_state("listening", message.response_id)
        elif isinstance(message, PingMessage):
            await self._send(PongMessage())
        elif isinstance(message, SessionEndMessage):
            self._end_reason = "client_ended"
            return False
        return True  # a repeated session.auth is ignored

    def _start_conversation(self) -> None:
        if self._started:
            return
        self._started = True
        handle = self._controller.new_response()
        handle.task = self._spawn(self._greet(handle))

    def _start_user_turn(self, text: str) -> None:
        if not text:
            return
        handle = self._controller.new_response()
        handle.task = self._spawn(self._user_turn(handle, text))

    def _spawn(self, coro: object) -> asyncio.Task[None]:
        task: asyncio.Task[None] = asyncio.create_task(coro)  # type: ignore[arg-type]
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def _interrupt(self) -> None:
        handle = await self._controller.cancel_active()
        if handle is None:
            return
        if handle.stream_complete:
            # Fully generated, but the browser was still speaking it.
            self._conversation.mark_last_assistant_interrupted()
        reason = "barge_in" if handle.first_audio_at is not None else "user_continued"
        logger.info("Caller interrupted", extra={"call_id": self._session.call_id, "reason": reason})
        await self._send(ResponseInterruptedMessage(response_id=handle.response_id, reason=reason))
        await self._send_state("listening", handle.response_id)

    # ------------------------------------------------------------------ turns
    async def _greet(self, handle: ResponseHandle) -> None:
        try:
            await self._deliver(handle, _single(self._services.prompts.greeting), "greeting")
        except ConnectionClosedError:
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Greeting failed", extra={"call_id": self._session.call_id})
            await self._send_error("internal_error", "The greeting could not be delivered.")

    async def _user_turn(self, handle: ResponseHandle, text: str) -> None:
        try:
            self._conversation.add_user(text)
            if self._settings.log_transcripts:
                logger.info("Caller said", extra={"call_id": self._session.call_id, "text": text})
            await self._send_state("thinking", handle.response_id)
            context = await self._retrieve_context()
            deltas = self._services.chat.stream_reply(self._conversation.to_llm_messages(), context)
            await self._deliver(handle, deltas, "answer")
        except asyncio.CancelledError:
            raise
        except ConnectionClosedError:
            return
        except Exception:
            logger.exception("Caller turn failed", extra={"call_id": self._session.call_id})
            await self._send_error("internal_error", "Something went wrong answering that.")
            with contextlib.suppress(ConnectionClosedError):
                await self._send_state("listening", handle.response_id)

    async def _retrieve_context(self) -> str | None:
        query = build_retrieval_query(
            self._conversation.pending_user_text, self._conversation.previous_user_texts()
        )
        try:
            chunks = await self._services.retriever.retrieve(query)
        except Exception as exc:
            logger.warning(
                "Knowledge retrieval failed",
                extra={"error": exc.__class__.__name__, "reason": str(exc)[:400]},
            )
            await self._send_error("knowledge_unavailable", "The knowledge base is unavailable right now.")
            return None
        return self._services.context_builder.build(chunks).text or None

    async def _deliver(self, handle: ResponseHandle, source: AsyncIterator[str], kind: ResponseKind) -> None:
        """Stream the reply text and, sentence by sentence, what the browser should say."""
        rid = handle.response_id
        await self._send(ResponseStartMessage(response_id=rid, kind=kind))
        full: list[str] = []
        segmenter = SpeechSegmenter()

        async def speak(segment: str) -> None:
            speech = prepare_text_for_speech(segment)
            if not speech:
                return
            if handle.first_audio_at is None:
                await self._send_state("speaking", rid)
            self._controller.note_audio(handle, estimate_speech_seconds(speech))
            handle.spoken_segments.append(speech)
            await self._send(ResponseSentenceMessage(response_id=rid, text=speech))

        async def emit(delta: str) -> None:
            full.append(delta)
            await self._send(ResponseTextDeltaMessage(response_id=rid, text=delta))
            for segment in segmenter.feed(delta):
                await speak(segment)

        try:
            try:
                async for delta in source:
                    await emit(delta)
            except HaumexError as exc:
                code = _error_code(exc, "llm_failed")
                logger.warning("Response generation failed", extra={"code": code, "reason": str(exc)[:400]})
                await self._send_error(code, "The assistant could not generate an answer.")
                if not full:
                    await emit(self._services.prompts.unavailable_message)
            for segment in segmenter.flush():
                await speak(segment)
        except asyncio.CancelledError:
            self._conversation.add_assistant(" ".join(handle.spoken_segments), interrupted=True)
            raise

        text = "".join(full).strip()
        self._conversation.add_assistant(text)
        handle.stream_complete = True
        await self._send(ResponseTextDoneMessage(response_id=rid, text=text))
        if handle.first_audio_at is None:
            handle.playback_finished = True
            await self._send_state("listening", rid)
        # Otherwise the browser reports playback.finished, which moves the state to "listening".
