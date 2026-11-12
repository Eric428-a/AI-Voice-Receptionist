"""Bounded conversation memory for one call."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

Role = Literal["user", "assistant"]
INTERRUPTION_MARKER = "[interrupted by the caller]"


@dataclass(slots=True)
class Turn:
    role: Role
    content: str
    interrupted: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Conversation:
    def __init__(self, max_turns: int = 24) -> None:
        self._turns: list[Turn] = []
        self._max_turns = max_turns

    @property
    def turns(self) -> list[Turn]:
        return list(self._turns)

    def _append(self, turn: Turn) -> None:
        self._turns.append(turn)
        if len(self._turns) > self._max_turns:
            self._turns = self._turns[-self._max_turns :]

    def add_user(self, text: str) -> None:
        text = text.strip()
        if text:
            self._append(Turn("user", text))

    def add_assistant(self, text: str, interrupted: bool = False) -> None:
        text = text.strip()
        if text:
            self._append(Turn("assistant", text, interrupted=interrupted))

    def mark_last_assistant_interrupted(self) -> None:
        """The caller cut in while the last reply was still being spoken."""
        for turn in reversed(self._turns):
            if turn.role == "assistant":
                turn.interrupted = True
                return
            return  # caller already spoke after it; nothing to mark

    @property
    def awaiting_reply(self) -> bool:
        return bool(self._turns) and self._turns[-1].role == "user"

    def _pending_index(self) -> int:
        index = len(self._turns)
        while index > 0 and self._turns[index - 1].role == "user":
            index -= 1
        return index

    @property
    def pending_user_text(self) -> str:
        """Everything the caller said since the assistant last spoke."""
        return " ".join(t.content for t in self._turns[self._pending_index() :])

    def previous_user_texts(self, limit: int = 2) -> list[str]:
        earlier = [t.content for t in self._turns[: self._pending_index()] if t.role == "user"]
        return earlier[-limit:]

    def to_llm_messages(self) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for turn in self._turns:
            content = f"{turn.content} {INTERRUPTION_MARKER}" if turn.interrupted else turn.content
            if messages and messages[-1]["role"] == turn.role:
                messages[-1]["content"] += f" {content}"
            else:
                messages.append({"role": turn.role, "content": content})
        return messages
