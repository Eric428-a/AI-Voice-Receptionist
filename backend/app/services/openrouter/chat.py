"""Chat service: combines system prompt, retrieved knowledge and history."""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.core.config import Settings
from app.services.openrouter.client import ChatMessage, OpenRouterClient
from app.services.openrouter.prompts import PromptLibrary


class ChatService:
    def __init__(self, client: OpenRouterClient, prompts: PromptLibrary, settings: Settings) -> None:
        self._client = client
        self._prompts = prompts
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._client.configured

    def build_messages(
        self, history: list[ChatMessage], knowledge_context: str | None
    ) -> list[ChatMessage]:
        return [{"role": "system", "content": self._prompts.system_prompt(knowledge_context)}, *history]

    def stream_reply(
        self, history: list[ChatMessage], knowledge_context: str | None
    ) -> AsyncIterator[str]:
        return self._client.stream_chat(
            self.build_messages(history, knowledge_context),
            temperature=self._settings.llm_temperature,
            max_tokens=self._settings.llm_max_tokens,
        )
