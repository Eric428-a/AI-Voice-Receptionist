"""GET /api/health"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import __version__
from app.api.deps import get_container
from app.services.container import ServiceContainer

router = APIRouter(tags=["health"])


class ProviderHealth(BaseModel):
    llm: bool
    llm_model: str | None
    embedding_model: str
    speech: str


class KnowledgeHealth(BaseModel):
    indexed_documents: int
    ingestion_running: bool


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    environment: str
    active_calls: int
    providers: ProviderHealth
    knowledge: KnowledgeHealth


@router.get("/health", response_model=HealthResponse)
async def health(container: ServiceContainer = Depends(get_container)) -> HealthResponse:
    s = container.settings
    providers = ProviderHealth(
        llm=container.chat.configured,
        llm_model=s.openrouter_model or None,
        embedding_model=container.embedder.identifier,
        speech=f"browser ({s.speech_language})",
    )
    return HealthResponse(
        status="ok" if providers.llm else "degraded",
        service=s.company_name + " AI Voice Caller",
        version=__version__,
        environment=s.environment,
        active_calls=container.sessions.active_count,
        providers=providers,
        knowledge=KnowledgeHealth(
            indexed_documents=container.ingestion.indexed_document_count,
            ingestion_running=container.ingestion.running,
        ),
    )
