"""WebSocket message protocol (JSON text frames only - no audio).

The browser does speech recognition and speech synthesis itself; the backend
and OpenRouter only ever see text. Mirrors ``frontend/src/types/websocket.ts``.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

# --------------------------------------------------------------------------
# Client -> server
# --------------------------------------------------------------------------


class SessionAuthMessage(BaseModel):
    type: Literal["session.auth"]
    token: str = Field(min_length=8, max_length=256)


class SessionStartMessage(BaseModel):
    type: Literal["session.start"]


class UserSpeakingMessage(BaseModel):
    """The caller started talking (barge-in): stop the current reply."""

    type: Literal["user.speaking"]


class UserTextMessage(BaseModel):
    """Final transcript of one caller utterance, recognized in the browser."""

    type: Literal["user.text"]
    text: str = Field(min_length=1, max_length=2000)


class PlaybackFinishedMessage(BaseModel):
    type: Literal["playback.finished"]
    response_id: int = Field(ge=0)


class SessionEndMessage(BaseModel):
    type: Literal["session.end"]


class PingMessage(BaseModel):
    type: Literal["ping"]


ClientMessage = Annotated[
    Union[
        SessionAuthMessage,
        SessionStartMessage,
        UserSpeakingMessage,
        UserTextMessage,
        PlaybackFinishedMessage,
        SessionEndMessage,
        PingMessage,
    ],
    Field(discriminator="type"),
]

client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(raw: str) -> ClientMessage:
    """Parse and validate a client JSON message (raises ``pydantic.ValidationError``)."""
    return client_message_adapter.validate_json(raw)


# --------------------------------------------------------------------------
# Server -> client
# --------------------------------------------------------------------------

AssistantState = Literal["listening", "thinking", "speaking"]
ResponseKind = Literal["greeting", "answer", "fallback"]


class Capabilities(BaseModel):
    llm: bool
    knowledge_documents: int


class SpeechSettings(BaseModel):
    language: str
    rate: float


class SessionReadyMessage(BaseModel):
    type: Literal["session.ready"] = "session.ready"
    call_id: str
    capabilities: Capabilities
    speech: SpeechSettings
    max_duration_seconds: int


class StateMessage(BaseModel):
    type: Literal["state"] = "state"
    state: AssistantState
    response_id: int | None = None


class ResponseStartMessage(BaseModel):
    type: Literal["response.start"] = "response.start"
    response_id: int
    kind: ResponseKind


class ResponseTextDeltaMessage(BaseModel):
    type: Literal["response.text.delta"] = "response.text.delta"
    response_id: int
    text: str


class ResponseSentenceMessage(BaseModel):
    """One speakable sentence for the browser's speech synthesis."""

    type: Literal["response.sentence"] = "response.sentence"
    response_id: int
    text: str


class ResponseTextDoneMessage(BaseModel):
    type: Literal["response.text.done"] = "response.text.done"
    response_id: int
    text: str


class ResponseInterruptedMessage(BaseModel):
    type: Literal["response.interrupted"] = "response.interrupted"
    response_id: int
    reason: Literal["barge_in", "user_continued"]


ErrorCode = Literal[
    "invalid_message",
    "unauthorized",
    "llm_unavailable",
    "llm_failed",
    "knowledge_unavailable",
    "internal_error",
]


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    code: ErrorCode
    message: str
    recoverable: bool = True


SessionEndReason = Literal[
    "client_ended", "server_ended", "max_duration", "unauthorized", "setup_timeout", "error"
]


class SessionEndedMessage(BaseModel):
    type: Literal["session.ended"] = "session.ended"
    reason: SessionEndReason


class PongMessage(BaseModel):
    type: Literal["pong"] = "pong"


ServerMessage = Union[
    SessionReadyMessage,
    StateMessage,
    ResponseStartMessage,
    ResponseTextDeltaMessage,
    ResponseSentenceMessage,
    ResponseTextDoneMessage,
    ResponseInterruptedMessage,
    ErrorMessage,
    SessionEndedMessage,
    PongMessage,
]
