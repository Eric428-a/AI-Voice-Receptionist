"""Builds and owns every long-lived service (one instance per app)."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.calls.call_manager import CallServices
from app.services.calls.session_manager import CallSessionManager
from app.services.openrouter.chat import ChatService
from app.services.openrouter.client import OpenRouterClient
from app.services.openrouter.prompts import PromptLibrary
from app.services.rag.context_builder import ContextBuilder
from app.services.rag.embeddings import EmbeddingProvider, create_embedding_provider
from app.services.rag.ingestion import IngestionService, KnowledgeWatcher
from app.services.rag.retriever import Retriever
from app.services.rag.vector_store import ChromaVectorStore

logger = get_logger(__name__)


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    openrouter: OpenRouterClient
    prompts: PromptLibrary
    chat: ChatService
    embedder: EmbeddingProvider
    vector_store: ChromaVectorStore
    ingestion: IngestionService
    retriever: Retriever
    context_builder: ContextBuilder
    sessions: CallSessionManager
    watcher: KnowledgeWatcher | None = None

    def call_services(self) -> CallServices:
        return CallServices(
            settings=self.settings,
            sessions=self.sessions,
            chat=self.chat,
            prompts=self.prompts,
            retriever=self.retriever,
            context_builder=self.context_builder,
            knowledge_documents=self.ingestion.indexed_document_count,
        )

    async def aclose(self) -> None:
        if self.watcher:
            await self.watcher.stop()
        await self.sessions.shutdown()
        for closable in (self.openrouter, self.embedder):
            try:
                await closable.aclose()
            except Exception:  # pragma: no cover - best-effort shutdown
                logger.exception("Error while closing service")


def build_container(
    settings: Settings,
    *,
    embedder: EmbeddingProvider | None = None,
    openrouter: OpenRouterClient | None = None,
) -> ServiceContainer:
    """Create all services. Optional overrides make testing with fakes easy."""
    settings.vector_store_path.mkdir(parents=True, exist_ok=True)
    openrouter = openrouter or OpenRouterClient(
        api_key=settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else None,
        base_url=settings.openrouter_base_url,
        model=settings.openrouter_model,
        timeout_seconds=settings.openrouter_timeout_seconds,
        connect_timeout_seconds=settings.openrouter_connect_timeout_seconds,
        max_retries=settings.openrouter_max_retries,
        app_url=settings.openrouter_app_url,
        app_title=settings.openrouter_app_title,
    )
    prompts = PromptLibrary(settings)
    embedder = embedder or create_embedding_provider(settings)
    store = ChromaVectorStore(settings.vector_store_path, settings.chroma_collection)
    ingestion = IngestionService(settings, embedder, store)
    return ServiceContainer(
        settings=settings,
        openrouter=openrouter,
        prompts=prompts,
        chat=ChatService(openrouter, prompts, settings),
        embedder=embedder,
        vector_store=store,
        ingestion=ingestion,
        retriever=Retriever(embedder, store, settings),
        context_builder=ContextBuilder(settings.max_context_chars),
        sessions=CallSessionManager(settings),
    )
