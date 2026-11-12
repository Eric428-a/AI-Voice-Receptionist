"""Semantic retrieval over the unified knowledge index."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.rag.embeddings import EmbeddingProvider
from app.services.rag.vector_store import ChromaVectorStore, Metadata

logger = get_logger(__name__)

_WORD = re.compile(r"\w+")
SHORT_QUERY_WORDS = 6


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float
    filename: str
    relative_path: str
    page_number: int


def build_retrieval_query(current: str, previous_user_turns: list[str]) -> str:
    """Make short follow-ups ("what kind?") searchable by adding recent context.

    The current utterance is placed last so it dominates; at most two earlier
    caller turns are prepended when the current one is short.
    """
    current = current.strip()
    if len(_WORD.findall(current)) >= SHORT_QUERY_WORDS or not previous_user_turns:
        return current
    context = " ".join(t.strip() for t in previous_user_turns[-2:] if t.strip())
    return f"{context} {current}".strip()


class Retriever:
    def __init__(self, embedder: EmbeddingProvider, store: ChromaVectorStore, settings: Settings) -> None:
        self._embedder = embedder
        self._store = store
        self._top_k = settings.retrieval_top_k
        self._max_chunks = settings.retrieval_max_chunks
        self._min_score = settings.retrieval_min_score

    async def retrieve(self, query: str, where: Metadata | None = None) -> list[RetrievedChunk]:
        query = query.strip()
        if not query or await self._store.count() == 0:
            return []
        embedding = await self._embedder.embed_query(query)
        hits = await self._store.query(embedding, self._top_k, where=where)
        results: list[RetrievedChunk] = []
        seen_texts: set[str] = set()
        for hit in sorted(hits, key=lambda h: h.score, reverse=True):
            if hit.score < self._min_score:
                continue
            fingerprint = " ".join(hit.text.lower().split())[:300]
            if fingerprint in seen_texts:
                continue
            seen_texts.add(fingerprint)
            results.append(
                RetrievedChunk(
                    chunk_id=hit.chunk_id,
                    text=hit.text,
                    score=hit.score,
                    filename=str(hit.metadata.get("filename", "")),
                    relative_path=str(hit.metadata.get("relative_path", "")),
                    page_number=int(hit.metadata.get("page_number", 0) or 0),
                )
            )
            if len(results) >= self._max_chunks:
                break
        logger.debug("Retrieved chunks", extra={"count": len(results)})
        return results
