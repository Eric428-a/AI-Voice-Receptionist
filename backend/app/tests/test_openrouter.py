from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ProviderNotConfiguredError
from app.services.openrouter.chat import ChatService
from app.services.openrouter.client import OpenRouterClient, OpenRouterError
from app.services.openrouter.prompts import NO_KNOWLEDGE_NOTE, PromptLibrary
from app.tests.conftest import make_openrouter, sse_body


async def _collect(client: OpenRouterClient, **kwargs: object) -> str:
    return "".join([d async for d in client.stream_chat([{"role": "user", "content": "hi"}], **kwargs)])  # type: ignore[arg-type]


async def test_streaming_parses_sse_and_sends_headers() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, content=sse_body(["Hello", " there", "!"]),
                              headers={"content-type": "text/event-stream"})

    client = make_openrouter(handler)
    assert await _collect(client) == "Hello there!"
    request = captured[0]
    assert str(request.url) == "https://openrouter.test/api/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["X-Title"] == "Haumex Labs Inc. AI Voice Caller"
    body = json.loads(request.content)
    assert body["stream"] is True and body["model"] == "test/model"
    await client.aclose()


async def test_retries_transient_errors_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, json={"error": {"message": "rate limited"}}, headers={"Retry-After": "0"})
        return httpx.Response(200, content=sse_body(["ok"]))

    client = make_openrouter(handler, max_retries=2)
    assert await _collect(client) == "ok"
    assert attempts["n"] == 3
    await client.aclose()


async def test_non_retryable_error_raises_immediately() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    client = make_openrouter(handler)
    with pytest.raises(OpenRouterError) as info:
        await _collect(client)
    assert info.value.status_code == 401 and "bad key" in info.value.message
    assert attempts["n"] == 1
    await client.aclose()


async def test_in_stream_error_is_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = sse_body(["partial"]).replace(
            b"data: [DONE]", b'data: {"error": {"message": "provider crashed"}}'
        )
        return httpx.Response(200, content=body)

    client = make_openrouter(handler)
    with pytest.raises(OpenRouterError, match="provider crashed"):
        await _collect(client)
    await client.aclose()


async def test_missing_configuration_fails_gracefully() -> None:
    client = make_openrouter(lambda r: httpx.Response(200), api_key="", model="")
    assert client.configured is False
    with pytest.raises(ProviderNotConfiguredError):
        await _collect(client)
    await client.aclose()


async def test_cancellation_stops_consuming_stream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body([f"t{i} " for i in range(50)]))

    client = make_openrouter(handler)
    stream = client.stream_chat([{"role": "user", "content": "hi"}])
    received = [await stream.__anext__(), await stream.__anext__()]
    await stream.aclose()  # what task cancellation does on barge-in
    assert received == ["t0 ", "t1 "]
    await client.aclose()


async def test_complete_returns_message_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is False
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "done"}}]})

    client = make_openrouter(handler)
    assert await client.complete([{"role": "user", "content": "x"}]) == "done"
    await client.aclose()


def test_system_prompt_grounding(settings: Settings) -> None:
    prompts = PromptLibrary(settings)
    with_context = prompts.system_prompt("[Excerpt 1 | profile, page 1]\nWe build robots.")
    assert "Haumex Labs Inc." in with_context and "We build robots." in with_context
    assert "{company_name}" not in with_context
    assert NO_KNOWLEDGE_NOTE in prompts.system_prompt(None)


async def test_chat_service_builds_messages(settings: Settings) -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=sse_body(["Yes."]))

    client = make_openrouter(handler)
    chat = ChatService(client, PromptLibrary(settings), settings)
    history = [{"role": "assistant", "content": "Hello!"}, {"role": "user", "content": "Do you work on AI?"}]
    reply = "".join([d async for d in chat.stream_reply(history, "context text")])
    assert reply == "Yes."
    messages = sent[0]["messages"]
    assert messages[0]["role"] == "system" and "context text" in messages[0]["content"]  # type: ignore[index]
    assert messages[1:] == history  # type: ignore[index]
    await client.aclose()
