"""Sentence-aware text chunking with overlap, preserving page numbers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.rag.pdf_extractor import ExtractedPage

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")
_HAS_WORD = re.compile(r"[A-Za-z0-9]{2,}")


@dataclass(frozen=True, slots=True)
class TextChunk:
    text: str
    page_number: int
    chunk_index: int  # index within the whole document


class TextChunker:
    def __init__(self, chunk_size: int = 1000, overlap: int = 150, min_chunk_chars: int = 20) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not 0 <= overlap < chunk_size:
            raise ValueError("overlap must be >= 0 and smaller than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk_chars = min_chunk_chars

    def _hard_split(self, text: str) -> list[str]:
        pieces: list[str] = []
        current = ""
        for word in text.split():
            while len(word) > self.chunk_size:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.append(word[: self.chunk_size])
                word = word[self.chunk_size :]
            candidate = f"{current} {word}" if current else word
            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                pieces.append(current)
                current = word
        if current:
            pieces.append(current)
        return pieces

    def _units(self, text: str) -> list[str]:
        units: list[str] = []
        for paragraph in re.split(r"\n\s*\n", text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            for sentence in _SENTENCE_SPLIT.split(paragraph):
                sentence = sentence.strip()
                if not sentence:
                    continue
                if len(sentence) > self.chunk_size:
                    units.extend(self._hard_split(sentence))
                else:
                    units.append(sentence)
        return units

    def _tail(self, text: str) -> str:
        if self.overlap == 0 or len(text) <= self.overlap:
            return text if self.overlap else ""
        tail = text[-self.overlap :]
        space = tail.find(" ")
        return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail

    def chunk_text(self, text: str) -> list[str]:
        chunks: list[str] = []
        current = ""
        for unit in self._units(text):
            candidate = f"{current} {unit}" if current else unit
            if len(candidate) <= self.chunk_size:
                current = candidate
                continue
            if current:
                chunks.append(current)
            overlap = self._tail(current) if current else ""
            current = f"{overlap} {unit}".strip() if overlap else unit
            if len(current) > self.chunk_size:
                current = unit
        if current:
            chunks.append(current)
        return [c for c in chunks if len(c) >= self.min_chunk_chars and _HAS_WORD.search(c)]

    def chunk_pages(self, pages: list[ExtractedPage]) -> list[TextChunk]:
        result: list[TextChunk] = []
        for page in pages:
            for text in self.chunk_text(page.text):
                result.append(TextChunk(text=text, page_number=page.page_number, chunk_index=len(result)))
        return result
