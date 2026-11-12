from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.container import build_container
from app.tests.conftest import FakeEmbedder


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, lambda s: build_container(s, embedder=FakeEmbedder())))


def test_health_reports_degraded_without_credentials(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["service"] == "Haumex Labs Inc. AI Voice Caller"
    assert body["providers"] == {
        "llm": False, "llm_model": None, "embedding_model": "fake:bow-256", "speech": "browser (en-US)",
    }
    assert body["active_calls"] == 0
    assert body["knowledge"]["indexed_documents"] == 0


def test_health_never_exposes_secrets(settings: Settings) -> None:
    settings.openrouter_api_key = None
    with _client(settings) as client:
        text = client.get("/api/health").text
    assert "admin-secret-key" not in text


def test_greeting_uses_company_name(settings: Settings) -> None:
    assert "Haumex Labs" in settings.greeting_text
    assert "{" not in settings.greeting_text


def test_chunk_size_alias(tmp_path) -> None:  # type: ignore[no-untyped-def]
    s = Settings(_env_file=None, CHUNK_SIZE=800, knowledge_directory=tmp_path)  # type: ignore[call-arg]
    assert s.rag_chunk_size == 800
