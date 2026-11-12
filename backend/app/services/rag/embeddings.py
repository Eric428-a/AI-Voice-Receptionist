"""Embeddings for PDF search.

``fastembed`` runs a small open model (default ``BAAI/bge-small-en-v1.5``)
locally on this computer: no account, no API key and no external AI provider.
The model files are downloaded once from Hugging Face into
``data/vector_store/models``. The provider interface is kept so another
backend can be added later without touching the rest of the RAG code.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import Settings
from app.core.errors import ProviderError, ProviderNotConfiguredError
from app.core.logging import get_logger

logger = get_logger(__name__)

Vector = list[float]


class EmbeddingError(ProviderError):
    code = "knowledge_unavailable"


class EmbeddingProvider(ABC):
    provider_name: str = "base"

    def __init__(self, model: str) -> None:
        self.model = model

    @property
    def identifier(self) -> str:
        """Stable id stored with the index; changing it forces a full re-index."""
        return f"{self.provider_name}:{self.model}"

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[Vector]: ...

    @abstractmethod
    async def embed_query(self, text: str) -> Vector: ...

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        return None


class FastEmbedProvider(EmbeddingProvider):
    provider_name = "fastembed"

    def __init__(self, model: str, batch_size: int = 32, cache_dir: str | None = None) -> None:
        super().__init__(model)
        self._batch_size = batch_size
        self._cache_dir = cache_dir
        self._model: Any = None
        self._lock = asyncio.Lock()

    def _load(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover - depends on install
                raise ProviderNotConfiguredError(
                    "fastembed is not installed. Run: pip install fastembed",
                    code="knowledge_unavailable",
                ) from exc
            logger.info("Loading local embedding model", extra={"model": self.model})
            try:
                self._model = TextEmbedding(model_name=self.model, cache_dir=self._cache_dir)
            except Exception as exc:
                raise EmbeddingError(f"Could not load embedding model '{self.model}': {exc}") from exc
        return self._model

    async def embed_documents(self, texts: list[str]) -> list[Vector]:
        if not texts:
            return []
        async with self._lock:
            def run() -> list[Vector]:
                model = self._load()
                return [v.tolist() for v in model.passage_embed(texts, batch_size=self._batch_size)]
            return await asyncio.to_thread(run)

    async def embed_query(self, text: str) -> Vector:
        async with self._lock:
            def run() -> Vector:
                model = self._load()
                return next(iter(model.query_embed(text))).tolist()
            return await asyncio.to_thread(run)


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    return FastEmbedProvider(
        model=settings.embedding_model,
        batch_size=settings.embedding_batch_size,
        cache_dir=str(settings.vector_store_path / "models"),
    )
