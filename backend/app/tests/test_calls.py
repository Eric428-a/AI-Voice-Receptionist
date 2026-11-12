from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.core.config import Settings
from app.main import create_app
from app.services.calls.conversation import INTERRUPTION_MARKER, Conversation
from app.services.container import ServiceContainer, build_container
from app.services.voice.interruption import InterruptionController
from app.services.voice.text_to_speech import SpeechSegmenter, estimate_speech_seconds, prepare_text_for_speech
from app.tests.conftest import FakeEmbedder, make_openrouter, sse_body

# ------------------------------------------------------------------ unit tests


def test_speech_segmenter_handles_abbreviations() -> None:
    seg = SpeechSegmenter()
    out = seg.feed("Haumex Labs Inc. works on AI. We also do ")
    out += seg.feed("robotics! Anything else?")
    out += seg.flush()
    assert out == ["Haumex Labs Inc. works on AI.", "We also do robotics!", "Anything else?"]


def test_prepare_text_for_speech() -> None:
    raw = "**Yes!** See [our site](https://example.com) or https://x.y\n- item one"
    assert prepare_text_for_speech(raw) == "Yes! See our site or item one"
    assert 1.0 < estimate_speech_seconds("one two three four five") < 3.0


def test_conversation_context_and_interruptions() -> None:
    convo = Conversation()
    convo.add_assistant("Hello, how can I help?")
    convo.add_user("Do you work on AI?")
    convo.add_assistant("Yes, AI is one of the areas", interrupted=True)
    convo.add_user("Wait, what about")
    convo.add_user("biomedical projects?")
    assert convo.awaiting_reply
    assert convo.pending_user_text == "Wait, what about biomedical projects?"
    assert convo.previous_user_texts() == ["Do you work on AI?"]
    messages = convo.to_llm_messages()
    assert messages[2]["content"].endswith(INTERRUPTION_MARKER)
    assert messages[3] == {"role": "user", "content": "Wait, what about biomedical projects?"}


async def test_interruption_controller_cancels_task() -> None:
    clock = {"t": 0.0}
    controller = InterruptionController(clock=lambda: clock["t"])
    handle = controller.new_response()
    handle.task = asyncio.create_task(asyncio.sleep(10))
    controller.note_audio(handle, 2.0)
    assert controller.is_active() and controller.is_audible()
    assert await controller.cancel_active() is handle and handle.task.cancelled()
    assert not controller.is_active()

    done = controller.new_response()
    controller.note_audio(done, 1.0)
    assert controller.is_audible()  # browser still speaking after generation finished
    clock["t"] = 5.0
    assert not controller.is_audible()  # time-based safety net


# ------------------------------------------------------------------ API + WebSocket


def build_client(settings: Settings,
                 llm_handler: Callable[[httpx.Request], Any] | None = None) -> TestClient:
    def factory(s: Settings) -> ServiceContainer:
        openrouter = make_openrouter(llm_handler) if llm_handler else None
        return build_container(s, embedder=FakeEmbedder(), openrouter=openrouter)

    return TestClient(create_app(settings, factory))


def receive_until(ws: WebSocketTestSession, predicate: Callable[[dict[str, Any]], bool],
                  limit: int = 200) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for _ in range(limit):
        message = ws.receive_json()
        messages.append(message)
        if predicate(message):
            return messages
    raise AssertionError(f"predicate not met; got {[m['type'] for m in messages]}")


@contextmanager
def open_call(client: TestClient) -> Iterator[tuple[dict[str, Any], WebSocketTestSession]]:
    """Authenticated call socket that is always closed, even when an assertion fails."""
    created = client.post("/api/calls").json()
    with client.websocket_connect(created["websocket_path"]) as ws:
        ws.send_json({"type": "session.auth", "token": created["session_token"]})
        try:
            yield created, ws
        finally:
            try:
                ws.send_json({"type": "session.end"})
            except Exception:
                pass


def finish_greeting(ws: WebSocketTestSession) -> None:
    ws.send_json({"type": "session.start"})
    msgs = receive_until(ws, lambda m: m["type"] == "response.text.done")
    ws.send_json({"type": "playback.finished", "response_id": msgs[-1]["response_id"]})
    receive_until(ws, lambda m: m["type"] == "state" and m["state"] == "listening")


def test_create_and_end_call(settings: Settings) -> None:
    with build_client(settings) as client:
        created = client.post("/api/calls")
        assert created.status_code == 201
        body = created.json()
        assert len(body["call_id"]) == 32 and body["session_token"]
        assert client.get("/api/health").json()["active_calls"] == 1
        assert client.delete(f"/api/calls/{body['call_id']}").status_code == 200
        assert client.delete(f"/api/calls/{body['call_id']}").status_code == 404
        assert client.delete("/api/calls/not-a-valid-id").status_code == 422


def test_call_capacity_limit(settings: Settings) -> None:
    settings.max_concurrent_calls = 1
    with build_client(settings) as client:
        assert client.post("/api/calls").status_code == 201
        assert client.post("/api/calls").status_code == 503


def test_websocket_rejects_bad_token(settings: Settings) -> None:
    with build_client(settings) as client:
        created = client.post("/api/calls").json()
        with client.websocket_connect(created["websocket_path"]) as ws:
            ws.send_json({"type": "session.auth", "token": "wrong-token-value"})
            error = ws.receive_json()
            assert error["type"] == "error" and error["code"] == "unauthorized"


def test_websocket_rejects_foreign_origin(settings: Settings) -> None:
    with build_client(settings) as client:
        created = client.post("/api/calls").json()
        with pytest.raises(Exception):
            with client.websocket_connect(created["websocket_path"], headers={"origin": "https://evil.example"}) as ws:
                ws.receive_json()


def test_greeting_and_answer_are_text_only(settings: Settings) -> None:
    sent: list[dict[str, Any]] = []

    def llm(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=sse_body(["We build AI software. ", "Anything else?"]))

    with build_client(settings, llm) as client:
        with open_call(client) as (_, ws):
            ready = ws.receive_json()
            assert ready["type"] == "session.ready"
            assert ready["capabilities"] == {"llm": True, "knowledge_documents": 0}
            assert ready["speech"] == {"language": "en-US", "rate": 1.0}

            ws.send_json({"type": "session.start"})
            msgs = receive_until(ws, lambda m: m["type"] == "response.text.done")
            assert msgs[0] == {"type": "response.start", "response_id": msgs[0]["response_id"], "kind": "greeting"}
            sentences = [m["text"] for m in msgs if m["type"] == "response.sentence"]
            assert sentences[0] == "Hello! Thank you for calling Haumex Labs."  # tiny fragments are merged
            assert "virtual assistant" in " ".join(sentences)
            ws.send_json({"type": "playback.finished", "response_id": msgs[-1]["response_id"]})
            receive_until(ws, lambda m: m["type"] == "state" and m["state"] == "listening")

            ws.send_json({"type": "user.text", "text": "What does Haumex Labs do?"})
            msgs = receive_until(ws, lambda m: m["type"] == "response.text.done")
            assert [m["state"] for m in msgs if m["type"] == "state"][:2] == ["thinking", "speaking"]
            assert [m["text"] for m in msgs if m["type"] == "response.sentence"] == [
                "We build AI software.", "Anything else?",
            ]
            assert msgs[-1]["text"] == "We build AI software. Anything else?"

            # Only text reached OpenRouter: no audio parts, no audio modalities.
            payload = sent[0]
            assert "modalities" not in payload and "audio" not in payload
            assert all(isinstance(m["content"], str) for m in payload["messages"])
            assert payload["messages"][-1] == {"role": "user", "content": "What does Haumex Labs do?"}
        time.sleep(0.1)
        assert client.get("/api/health").json()["active_calls"] == 0


def test_barge_in_stops_assistant_and_keeps_context(settings: Settings) -> None:
    requests: list[dict[str, Any]] = []

    def llm(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=sse_body(["Our biomedical work covers wearable sensors."]))

    with build_client(settings, llm) as client:
        with open_call(client) as (_, ws):
            ws.receive_json()
            ws.send_json({"type": "session.start"})
            msgs = receive_until(ws, lambda m: m["type"] == "response.text.done")
            greeting_id = msgs[0]["response_id"]

            # The browser is still speaking the greeting when the caller talks over it.
            ws.send_json({"type": "user.speaking"})
            msgs = receive_until(ws, lambda m: m["type"] == "response.interrupted")
            assert msgs[-1] == {"type": "response.interrupted", "response_id": greeting_id, "reason": "barge_in"}

            ws.send_json({"type": "user.text", "text": "Wait, what about your biomedical projects?"})
            msgs = receive_until(ws, lambda m: m["type"] == "response.text.done")
            assert msgs[-1]["text"] == "Our biomedical work covers wearable sensors."

            history = requests[-1]["messages"]
            assert history[1]["role"] == "assistant" and history[1]["content"].endswith(INTERRUPTION_MARKER)
            assert history[-1] == {"role": "user", "content": "Wait, what about your biomedical projects?"}


def test_caller_continuing_cancels_generation_and_merges_speech(settings: Settings) -> None:
    requests: list[dict[str, Any]] = []

    async def slow_body() -> AsyncIterator[bytes]:
        await asyncio.sleep(0.4)
        yield sse_body(["Slow answer."])

    async def llm(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(200, content=slow_body())
        return httpx.Response(200, content=sse_body(["Yes, in healthcare too."]))

    with build_client(settings, llm) as client:
        with open_call(client) as (_, ws):
            ws.receive_json()
            finish_greeting(ws)
            ws.send_json({"type": "user.text", "text": "Do you work on AI"})
            receive_until(ws, lambda m: m["type"] == "state" and m["state"] == "thinking")
            ws.send_json({"type": "user.speaking"})  # caller keeps talking before any reply
            msgs = receive_until(ws, lambda m: m["type"] == "response.interrupted")
            assert msgs[-1]["reason"] == "user_continued"
            ws.send_json({"type": "user.text", "text": "in healthcare?"})
            msgs = receive_until(ws, lambda m: m["type"] == "response.text.done")
            assert msgs[-1]["text"] == "Yes, in healthcare too."
            assert requests[-1]["messages"][-1] == {"role": "user", "content": "Do you work on AI in healthcare?"}


def test_missing_llm_credentials_degrade_gracefully(settings: Settings) -> None:
    with build_client(settings) as client:  # no OpenRouter key/model
        with open_call(client) as (_, ws):
            assert ws.receive_json()["capabilities"]["llm"] is False
            finish_greeting(ws)
            ws.send_json({"type": "user.text", "text": "Hello?"})
            msgs = receive_until(ws, lambda m: m["type"] == "response.text.done")
            error = next(m for m in msgs if m["type"] == "error")
            assert error["code"] == "llm_unavailable" and error["recoverable"] is True
            assert "trouble" in msgs[-1]["text"]


def test_invalid_messages_are_rejected(settings: Settings) -> None:
    with build_client(settings) as client:
        with open_call(client) as (_, ws):
            ws.receive_json()
            ws.send_text('{"type": "nonsense"}')
            assert ws.receive_json()["code"] == "invalid_message"
            ws.send_json({"type": "user.text", "text": ""})
            assert ws.receive_json()["code"] == "invalid_message"
            ws.send_bytes(b"\x00\x01")  # audio is never accepted; ignored
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}


def test_delete_endpoint_closes_live_socket(settings: Settings) -> None:
    with build_client(settings) as client:
        with open_call(client) as (created, ws):
            ws.receive_json()
            client.delete(f"/api/calls/{created['call_id']}")
            msgs = receive_until(ws, lambda m: m["type"] == "session.ended")
            assert msgs[-1]["reason"] == "client_ended"


def test_knowledge_endpoints(settings: Settings) -> None:
    from app.tests.conftest import write_pdf

    write_pdf(settings.knowledge_directory / "about.pdf", ["Haumex Labs Inc. company overview."])
    with build_client(settings) as client:
        assert client.post("/api/knowledge/ingest").status_code == 401
        assert client.post("/api/knowledge/ingest", headers={"X-Admin-Key": "nope"}).status_code == 401
        report = client.post("/api/knowledge/ingest", headers={"X-Admin-Key": "admin-secret-key"})
        assert report.status_code == 200 and report.json()["added"] == ["about.pdf"]
        status = client.get("/api/knowledge/status").json()
        assert status["indexed_documents"] == 1 and status["total_chunks"] >= 1
