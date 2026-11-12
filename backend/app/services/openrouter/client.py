"""Async OpenRouter client (OpenAI-compatible Chat Completions API).

Implements the documented ``POST {base}/chat/completions`` endpoint with
server-sent-event streaming, timeouts, retries for transient failures
(408/429/5xx and network errors, honouring ``Retry-After``), and clean
cancellation: closing the async generator aborts the HTTP stream.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import ProviderError, ProviderNotConfiguredError
from app.core.logging import get_logger

logger = get_logger(__name__)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 8.0

ChatMessage = dict[str, Any]


class OpenRouterError(ProviderError):
    """Error raised for failed OpenRouter requests."""

    code = "llm_failed"


class OpenRouterClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        connect_timeout_seconds: float = 10.0,
        max_retries: int = 2,
        app_url: str | None = None,
        app_title: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model.strip()
        self._max_retries = max_retries
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # Optional attribution headers documented by OpenRouter.
        if app_url:
            headers["HTTP-Referer"] = app_url
        if app_title:
            headers["X-Title"] = app_title
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds),
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self.model)

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _ensure_configured(self, model: str | None) -> str:
        if not self._api_key:
            raise ProviderNotConfiguredError(
                "OPENROUTER_API_KEY is not set.", code="llm_unavailable"
            )
        resolved = (model or self.model).strip()
        if not resolved:
            raise ProviderNotConfiguredError("OPENROUTER_MODEL is not set.", code="llm_unavailable")
        return resolved

    @staticmethod
    def _error_from_response(status_code: int, body: bytes) -> OpenRouterError:
        detail = body.decode("utf-8", errors="replace")[:500]
        try:
            parsed = json.loads(detail)
            if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                detail = str(parsed["error"].get("message", detail))
        except json.JSONDecodeError:
            pass
        return OpenRouterError(
            f"OpenRouter request failed ({status_code}): {detail}",
            status_code=status_code,
            retryable=status_code in RETRYABLE_STATUS,
        )

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(MAX_BACKOFF_SECONDS, max(0.0, float(retry_after)))
            except ValueError:
                pass
        return min(MAX_BACKOFF_SECONDS, 0.5 * (2**attempt))

    def _payload(
        self, messages: list[ChatMessage], model: str, temperature: float, max_tokens: int, stream: bool
    ) -> dict[str, Any]:
        return {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

    async def stream_deltas(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Stream ``choices[0].delta`` objects from a Chat Completions request.

        Retries (rate limits, 5xx, network errors) only happen before the first
        delta arrives. Closing the generator aborts the HTTP stream, which is
        how an interrupted reply is cancelled.
        """
        payload = {**payload, "stream": True}
        attempt = 0
        while True:
            yielded = False
            retry_after: str | None = None
            try:
                async with self._http.stream("POST", "chat/completions", json=payload) as response:
                    if response.status_code != 200:
                        retry_after = response.headers.get("Retry-After")
                        raise self._error_from_response(response.status_code, await response.aread())
                    async for line in response.aiter_lines():
                        if not line or line.startswith(":"):
                            continue  # blank line or SSE comment (keep-alive)
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            logger.warning("Skipping malformed SSE chunk from OpenRouter")
                            continue
                        if isinstance(chunk.get("error"), dict):
                            raise OpenRouterError(
                                f"OpenRouter stream error: {chunk['error'].get('message', 'unknown')}",
                                retryable=False,
                            )
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            if delta:
                                yielded = True
                                yield delta
                            if choice.get("finish_reason") == "error":
                                raise OpenRouterError("OpenRouter reported a generation error.")
                    return
            except OpenRouterError as exc:
                if yielded or not exc.retryable or attempt >= self._max_retries:
                    raise
                logger.warning("Retrying OpenRouter request", extra={"status": exc.status_code})
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if yielded or attempt >= self._max_retries:
                    raise OpenRouterError(
                        f"OpenRouter network error: {exc.__class__.__name__}", retryable=True
                    ) from exc
                logger.warning("Retrying OpenRouter request after network error")
            await asyncio.sleep(self._retry_delay(attempt, retry_after))
            attempt += 1

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.4,
        max_tokens: int = 350,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas of the assistant reply."""
        resolved_model = self._ensure_configured(model)
        payload = self._payload(messages, resolved_model, temperature, max_tokens, True)
        async for delta in self.stream_deltas(payload):
            content = delta.get("content")
            if isinstance(content, str) and content:
                yield content

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 350,
        model: str | None = None,
    ) -> str:
        """Non-streaming completion with retries. Returns the assistant text."""
        resolved_model = self._ensure_configured(model)
        payload = self._payload(messages, resolved_model, temperature, max_tokens, False)
        attempt = 0
        while True:
            try:
                response = await self._http.post("chat/completions", json=payload)
                if response.status_code != 200:
                    error = self._error_from_response(response.status_code, response.content)
                    if not error.retryable or attempt >= self._max_retries:
                        raise error
                    await asyncio.sleep(
                        self._retry_delay(attempt, response.headers.get("Retry-After"))
                    )
                    attempt += 1
                    continue
                body = response.json()
                if isinstance(body.get("error"), dict):
                    raise OpenRouterError(str(body["error"].get("message", "unknown error")))
                choices = body.get("choices") or []
                if not choices:
                    return ""
                content = (choices[0].get("message") or {}).get("content")
                return content if isinstance(content, str) else ""
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self._max_retries:
                    raise OpenRouterError(
                        f"OpenRouter network error: {exc.__class__.__name__}", retryable=True
                    ) from exc
                await asyncio.sleep(self._retry_delay(attempt, None))
                attempt += 1
