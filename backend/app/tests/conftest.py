"""Shared fixtures and fakes. No test talks to a real external API."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.services.openrouter.client import OpenRouterClient
from app.services.rag.embeddings import EmbeddingProvider

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "of", "to", "is", "in", "on", "for", "what", "does", "do", "we", "our", "you", "with"}


class FakeEmbedder(EmbeddingProvider):
    """Deterministic hashed bag-of-words embeddings (cosine-meaningful)."""

    provider_name = "fake"

    def __init__(self, model: str = "bow-256", dims: int = 256) -> None:
        super().__init__(model)
        self.dims = dims
        self.document_calls = 0

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        for token in _TOKEN.findall(text.lower()):
            if token in _STOP:
                continue
            index = int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dims
            vec[index] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def sse_body(deltas: list[str], comment: bool = True) -> bytes:
    import json

    lines = [": OPENROUTER PROCESSING\n\n"] if comment else []
    for delta in deltas:
        lines.append("data: " + json.dumps({"choices": [{"delta": {"content": delta}, "index": 0}]}) + "\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def make_openrouter(handler: Callable[[httpx.Request], httpx.Response], **kwargs: object) -> OpenRouterClient:
    return OpenRouterClient(
        api_key=str(kwargs.get("api_key", "test-key")),
        base_url="https://openrouter.test/api/v1",
        model=str(kwargs.get("model", "test/model")),
        max_retries=int(kwargs.get("max_retries", 2)),  # type: ignore[arg-type]
        app_url="http://localhost:5173",
        app_title="Haumex Labs Inc. AI Voice Caller",
        transport=httpx.MockTransport(handler),
    )


# ---------------------------------------------------------------- minimal PDF writer

def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: Path, pages: list[str]) -> Path:
    """Write a valid text PDF (one string per page, wrapped to short lines)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    objects: list[bytes] = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, text in enumerate(pages):
        words, lines, current = text.split(), [], ""
        for word in words:
            if len(current) + len(word) + 1 > 80:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            lines.append(current)
        ops = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"]
        for line in lines:
            ops.append(f"({_escape(line)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content_id = 4 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


# ---------------------------------------------------------------- fixtures

@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    (tmp_path / "pdfs").mkdir()
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        knowledge_directory=tmp_path / "pdfs",
        vector_store_path=tmp_path / "store",
        auto_ingest_on_startup=False,
        knowledge_watch_interval_seconds=0,
        openrouter_api_key=None,
        openrouter_model="",
        admin_api_key="admin-secret-key",
        rag_chunk_size=400,
        rag_chunk_overlap=60,
        retrieval_min_score=0.15,
        max_call_duration_seconds=60,
    )


@pytest.fixture()
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()
