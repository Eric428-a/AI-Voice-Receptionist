from __future__ import annotations

from app.core.config import Settings
from app.services.rag.context_builder import ContextBuilder
from app.services.rag.ingestion import IngestionService
from app.services.rag.retriever import RetrievedChunk, Retriever, build_retrieval_query
from app.services.rag.vector_store import ChromaVectorStore
from app.tests.conftest import FakeEmbedder, write_pdf


async def _indexed(settings: Settings, embedder: FakeEmbedder) -> Retriever:
    root = settings.knowledge_directory
    write_pdf(root / "overview.pdf", [
        "Haumex Labs Inc. develops artificial intelligence software for scientific computing.",
        "Our robotics division designs autonomous inspection drones.",
    ])
    write_pdf(root / "health" / "biomed.pdf", [
        "The biomedical projects focus on wearable cardiac monitoring sensors.",
    ])
    store = ChromaVectorStore(settings.vector_store_path, settings.chroma_collection)
    await IngestionService(settings, embedder, store).ingest()
    return Retriever(embedder, store, settings)


async def test_retrieves_across_documents(settings: Settings, fake_embedder: FakeEmbedder) -> None:
    retriever = await _indexed(settings, fake_embedder)
    biomed = await retriever.retrieve("Tell me about biomedical cardiac sensors")
    assert biomed and biomed[0].relative_path == "health/biomed.pdf"
    robots = await retriever.retrieve("robotics drones")
    assert robots and robots[0].relative_path == "overview.pdf" and robots[0].page_number == 2


async def test_irrelevant_queries_return_nothing(settings: Settings, fake_embedder: FakeEmbedder) -> None:
    retriever = await _indexed(settings, fake_embedder)
    assert await retriever.retrieve("pizza delivery opening hours") == []


async def test_empty_index_returns_nothing(settings: Settings, fake_embedder: FakeEmbedder) -> None:
    store = ChromaVectorStore(settings.vector_store_path, settings.chroma_collection)
    assert await Retriever(fake_embedder, store, settings).retrieve("anything") == []


async def test_metadata_filtering(settings: Settings, fake_embedder: FakeEmbedder) -> None:
    retriever = await _indexed(settings, fake_embedder)
    hits = await retriever.retrieve("software sensors drones", where={"relative_path": "health/biomed.pdf"})
    assert hits and all(h.relative_path == "health/biomed.pdf" for h in hits)


def test_follow_up_query_includes_context() -> None:
    assert build_retrieval_query("What kind?", ["Do you guys work on AI?"]) == "Do you guys work on AI? What kind?"
    long_question = "What kinds of biomedical research projects are you running this year?"
    assert build_retrieval_query(long_question, ["Earlier"]) == long_question
    assert build_retrieval_query("Hi", []) == "Hi"


def test_context_builder_formats_and_limits() -> None:
    chunks = [
        RetrievedChunk("1", "A" * 3000, 0.9, "company_profile.pdf", "company_profile.pdf", 2),
        RetrievedChunk("2", "B" * 3000, 0.8, "services-2025.pdf", "x/services-2025.pdf", 5),
        RetrievedChunk("3", "C" * 3000, 0.7, "other.pdf", "other.pdf", 1),
    ]
    context = ContextBuilder(max_chars=5000).build(chunks)
    assert "[Excerpt 1 | company profile, page 2]" in context.text
    assert len(context.text) <= 5000
    assert "C" * 100 not in context.text
    assert context.sources == ["company_profile.pdf", "x/services-2025.pdf"]
    assert ContextBuilder().build([]).has_knowledge is False
