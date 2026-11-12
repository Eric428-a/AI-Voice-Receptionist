"""Persistent ChromaDB vector store (cosine similarity).

Embeddings are always supplied by our own provider; Chroma's built-in
embedding function is disabled. Chroma calls are synchronous, so they run in a
worker thread behind a lock.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.logging import get_logger

logger = get_logger(__name__)

Metadata = dict[str, str | int | float | bool]


@dataclass(frozen=True, slots=True)
class StoredChunk:
    chunk_id: str
    text: str
    embedding: list[float]
    metadata: Metadata


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: str
    text: str
    metadata: Metadata
    distance: float

    @property
    def score(self) -> float:
        """Cosine similarity in [-1, 1] (Chroma returns cosine distance = 1 - similarity)."""
        return 1.0 - self.distance


class ChromaVectorStore:
    def __init__(self, path: Path, collection_name: str) -> None:
        self._path = path / "chroma"
        self._collection_name = collection_name
        self._lock = asyncio.Lock()
        self._path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(self._path), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._collection = self._get_collection()

    def _get_collection(self) -> Any:
        return self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )

    async def count(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._collection.count)

    async def upsert(self, chunks: list[StoredChunk], batch_size: int = 256) -> None:
        if not chunks:
            return
        async with self._lock:
            def run() -> None:
                for start in range(0, len(chunks), batch_size):
                    batch = chunks[start : start + batch_size]
                    self._collection.upsert(
                        ids=[c.chunk_id for c in batch],
                        embeddings=[c.embedding for c in batch],
                        documents=[c.text for c in batch],
                        metadatas=[c.metadata for c in batch],
                    )
            await asyncio.to_thread(run)

    async def delete_document(self, relative_path: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._collection.delete, where={"relative_path": relative_path})

    async def reset(self) -> None:
        """Drop and recreate the collection (used when the embedding model changes)."""
        async with self._lock:
            def run() -> None:
                try:
                    self._client.delete_collection(self._collection_name)
                except Exception:  # collection may not exist
                    pass
                self._collection = self._get_collection()
            await asyncio.to_thread(run)

    async def query(
        self, embedding: list[float], top_k: int, where: Metadata | None = None
    ) -> list[SearchHit]:
        async with self._lock:
            def run() -> dict[str, Any]:
                count = self._collection.count()
                if count == 0:
                    return {}
                return self._collection.query(
                    query_embeddings=[embedding],
                    n_results=min(top_k, count),
                    where=where or None,
                    include=["documents", "metadatas", "distances"],
                )
            result = await asyncio.to_thread(run)
        if not result:
            return []
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        return [
            SearchHit(chunk_id=i, text=d or "", metadata=dict(m or {}), distance=float(dist))
            for i, d, m, dist in zip(ids, documents, metadatas, distances, strict=False)
        ]
