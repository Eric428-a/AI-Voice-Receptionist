from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.rag.document_loader import discover_pdfs
from app.services.rag.ingestion import IngestionInProgressError, IngestionService, chunk_id_for
from app.services.rag.pdf_extractor import PdfExtractionError, clean_text, extract_pdf
from app.services.rag.text_chunker import TextChunker
from app.services.rag.vector_store import ChromaVectorStore
from app.tests.conftest import FakeEmbedder, write_pdf


def _service(settings: Settings, embedder: FakeEmbedder) -> IngestionService:
    store = ChromaVectorStore(settings.vector_store_path, settings.chroma_collection)
    return IngestionService(settings, embedder, store)


def test_recursive_discovery_without_categories(tmp_path: Path) -> None:
    root = tmp_path / "pdfs"
    write_pdf(root / "profile.pdf", ["Company profile"])
    write_pdf(root / "deep" / "nested" / "Research.PDF", ["Research"])
    write_pdf(root / ".hidden" / "secret.pdf", ["Hidden"])
    (root / "notes.txt").write_text("not a pdf")
    found = discover_pdfs(root)
    assert [d.relative_path for d in found] == ["deep/nested/Research.PDF", "profile.pdf"]


def test_discovery_missing_directory(tmp_path: Path) -> None:
    assert discover_pdfs(tmp_path / "missing") == []


def test_pdf_extraction_per_page(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path / "doc.pdf", ["First page about robotics.", "Second page about biomedical work."])
    doc = extract_pdf(pdf)
    assert doc.page_count == 2
    assert [p.page_number for p in doc.pages] == [1, 2]
    assert "robotics" in doc.pages[0].text
    assert "biomedical" in doc.pages[1].text


def test_pdf_extraction_rejects_garbage(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf")
    with pytest.raises(PdfExtractionError):
        extract_pdf(bad)


def test_clean_text() -> None:
    raw = "Haumex  Labs devel-\nops software.\nSecond line\n\n\n\nNew   paragraph\x00"
    assert clean_text(raw) == "Haumex Labs develops software. Second line\n\nNew paragraph"


def test_chunker_respects_size_and_overlap() -> None:
    chunker = TextChunker(chunk_size=200, overlap=40)
    text = " ".join(f"Sentence number {i} describes a distinct engineering topic." for i in range(30))
    chunks = chunker.chunk_text(text)
    assert len(chunks) > 3
    assert all(len(c) <= 200 for c in chunks)
    for previous, current in zip(chunks, chunks[1:]):
        tail_words = previous.split()[-2:]
        assert " ".join(tail_words) in current  # overlap carried forward


def test_chunker_hard_splits_long_sentences() -> None:
    chunker = TextChunker(chunk_size=200, overlap=20)
    chunks = chunker.chunk_text("word " * 300)
    assert all(len(c) <= 200 for c in chunks)


def test_chunker_keeps_page_numbers() -> None:
    from app.services.rag.pdf_extractor import ExtractedPage

    chunker = TextChunker(chunk_size=200, overlap=20)
    chunks = chunker.chunk_pages([ExtractedPage(1, "Alpha topic text here."), ExtractedPage(3, "Gamma topic text here.")])
    assert [(c.page_number, c.chunk_index) for c in chunks] == [(1, 0), (3, 1)]


def test_chunker_validates_arguments() -> None:
    with pytest.raises(ValueError):
        TextChunker(chunk_size=100, overlap=100)


async def test_incremental_ingestion_lifecycle(settings: Settings, fake_embedder: FakeEmbedder) -> None:
    root = settings.knowledge_directory
    write_pdf(root / "about.pdf", ["Haumex Labs Inc. builds AI software for engineering teams."])
    write_pdf(root / "team" / "research.pdf", ["The research group studies biomedical signal processing."])
    service = _service(settings, fake_embedder)

    report = await service.ingest()
    assert sorted(report.added) == ["about.pdf", "team/research.pdf"]
    assert report.total_chunks >= 2

    calls_before = fake_embedder.document_calls
    report = await service.ingest()
    assert sorted(report.unchanged) == ["about.pdf", "team/research.pdf"]
    assert fake_embedder.document_calls == calls_before  # unchanged files are not re-embedded

    write_pdf(root / "about.pdf", ["Haumex Labs Inc. now also offers robotics consulting."])
    (root / "team" / "research.pdf").unlink()
    report = await service.ingest()
    assert report.updated == ["about.pdf"]
    assert report.removed == ["team/research.pdf"]

    status = await service.status()
    assert status.indexed_documents == 1
    assert status.documents[0].status == "indexed"
    assert status.documents[0].document_hash


async def test_duplicates_are_indexed_once(settings: Settings, fake_embedder: FakeEmbedder) -> None:
    root = settings.knowledge_directory
    write_pdf(root / "a.pdf", ["Identical brochure content about services."])
    (root / "copy").mkdir()
    (root / "copy" / "b.pdf").write_bytes((root / "a.pdf").read_bytes())
    service = _service(settings, fake_embedder)
    report = await service.ingest()
    assert report.added == ["a.pdf"]
    assert report.duplicates == ["copy/b.pdf"]
    status = await service.status()
    duplicate = next(d for d in status.documents if d.relative_path == "copy/b.pdf")
    assert duplicate.duplicate_of == "a.pdf"


async def test_failed_and_oversized_documents_are_reported(settings: Settings, fake_embedder: FakeEmbedder) -> None:
    root = settings.knowledge_directory
    (root / "broken.pdf").write_bytes(b"%PDF-1.4 broken")
    write_pdf(root / "ok.pdf", ["Valid content about the company."])
    settings.max_pdf_size_mb = 0.0005  # ~500 bytes
    service = _service(settings, fake_embedder)
    report = await service.ingest()
    assert "broken.pdf" in report.failed
    assert report.failed["ok.pdf"] == "file too large"
    status = await service.status()
    assert status.failed_documents == 2
    assert status.indexed_documents == 0


async def test_embedding_model_change_forces_reindex(settings: Settings) -> None:
    write_pdf(settings.knowledge_directory / "doc.pdf", ["Some company content to embed."])
    await _service(settings, FakeEmbedder("model-a")).ingest()
    report = await _service(settings, FakeEmbedder("model-b")).ingest()
    assert report.full_reindex is True
    assert report.added == ["doc.pdf"]


async def test_concurrent_ingestion_is_rejected(settings: Settings, fake_embedder: FakeEmbedder) -> None:
    service = _service(settings, fake_embedder)
    async with service._lock:  # simulate a running ingestion
        with pytest.raises(IngestionInProgressError):
            await service.ingest()


def test_chunk_ids_are_stable() -> None:
    assert chunk_id_for("abc", 1) == chunk_id_for("abc", 1)
    assert chunk_id_for("abc", 1) != chunk_id_for("abc", 2)
