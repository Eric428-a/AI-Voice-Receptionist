"""Turns retrieved chunks into a compact, model-only context block."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.rag.retriever import RetrievedChunk
from app.utils.file_utils import humanize_filename


@dataclass(slots=True)
class KnowledgeContext:
    text: str
    sources: list[str] = field(default_factory=list)

    @property
    def has_knowledge(self) -> bool:
        return bool(self.text)


class ContextBuilder:
    def __init__(self, max_chars: int = 6000) -> None:
        self._max_chars = max_chars

    def build(self, chunks: list[RetrievedChunk]) -> KnowledgeContext:
        parts: list[str] = []
        sources: list[str] = []
        used = 0
        for index, chunk in enumerate(chunks, start=1):
            title = humanize_filename(chunk.filename) if chunk.filename else "Company document"
            header = f"[Excerpt {index} | {title}, page {chunk.page_number}]"
            body = chunk.text.strip()
            remaining = self._max_chars - used - len(header) - 2
            if remaining <= 80:
                break
            if len(body) > remaining:
                cut = body[: remaining - 4]
                if " " in cut:
                    cut = cut.rsplit(" ", 1)[0]
                body = cut + " ..."
            block = f"{header}\n{body}"
            parts.append(block)
            used += len(block) + 2
            if chunk.relative_path and chunk.relative_path not in sources:
                sources.append(chunk.relative_path)
        return KnowledgeContext(text="\n\n".join(parts), sources=sources)
